#!/usr/bin/env python3
"""Local Crucible live-services backend (Phase 1: menu features).

Stdlib-only HTTP server that emulates the retail Challenge / GameConfig /
Entitlement / Offer / Identity / Level services well enough for the
shipped menu UI to populate as it did in retail.

The client reaches it via a minimal pak patch (see tools/patch_services.py):
  * Ni.config resolves all service URLs to http://127.0.0.1:PORT/<Service>
  * dummy local credentials unblock the AWS-SigV4 request queue
    (signatures are accepted without verification)

Wire notes the client demands:
  * gameconfig endpoints return {"data": "<json-string>"} (double-encoded)
  * service endpoints return plain JSON objects
  * every response needs CORS headers (the UI fetches cross-origin)

Usage: python3 crucible_backend.py [--port 18876]
"""
import argparse
import copy
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import crucible_data as D

STATE = {
    "rerolls_left": D.SEED_REROLLS,
    "dailies": [d["ChallengeId"] for d in D.DAILIES],
    "credits": D.SEED_CREDITS,
    "entitlements": D.base_entitlements(),
    "premium": False,
}
STATE_LOCK = threading.Lock()


def gameconfig_wrap(payload):
    return {"data": json.dumps(payload)}


def running_totals(progression_ids):
    out = []
    for pid in progression_ids or []:
        out.append({"progressionId": pid,
                    "runningTotal": D.SEED_PROGRESS.get(pid, 0)})
    return {"runningTotals": out}


def handle_challenges(path, body):
    if path == "/dailyChallenges":
        with STATE_LOCK:
            return {"challenges": list(STATE["dailies"]),
                    "rerollsLeft": STATE["rerolls_left"]}
    if path == "/challenges":
        return running_totals((body or {}).get("progressionIds"))
    if path == "/useReroll":
        cid = (body or {}).get("challengeId")
        with STATE_LOCK:
            if STATE["rerolls_left"] <= 0:
                return {"challenges": list(STATE["dailies"]),
                        "rerollsLeft": 0}
            pool = [d["ChallengeId"] for d in D.DAILY_POOL
                    if d["ChallengeId"] not in STATE["dailies"]
                    and d["ChallengeId"] != cid]
            if cid in STATE["dailies"] and pool:
                STATE["dailies"] = [c if c != cid else pool[0]
                                    for c in STATE["dailies"]]
            STATE["rerolls_left"] -= 1
            return {"challenges": list(STATE["dailies"]),
                    "rerollsLeft": STATE["rerolls_left"]}
    return None


def handle_gameconfig(path):
    if path == "/gameConfigData/feature-flags":
        # Keys map via f_: strip leading "ui", lowercase ("uiBattlepassEnabled"
        # -> "battlepassenabled"); drives nav-tab enable incl. BATTLE PASS.
        return gameconfig_wrap({"uiBattlepassEnabled": True,
                                 "uiSurrenderEnabled": True})
    if path == "/gameConfigData/seasons":
        return gameconfig_wrap([D.SEASON])
    if path == "/gameConfigData/ui-battlepass-tier-entitlements":
        return gameconfig_wrap(D.battlepass_tier_entitlements())
    if path == "/gameConfigData/ui-challenge-stars":
        return gameconfig_wrap(D.season_stars_table())
    if path == "/gameConfigData/ui-daily-challenge-stars":
        return gameconfig_wrap(D.daily_stars_table())
    if path == "/gameConfigData/ui-entitlement-to-achievement":
        return gameconfig_wrap(D.entitlement_to_achievement())
    if path == "/gameConfigData/ui-achievements":
        return gameconfig_wrap({})
    if path == "/gameConfigData/ui-ftue":
        return gameconfig_wrap({})
    if path == "/gameConfigData/entitlements":
        return gameconfig_wrap(D.entitlement_catalog())
    if path == "/gameConfigData/CharacterLevelUpRewards":
        return gameconfig_wrap([])
    if path == "/gameConfigData/ui-rewards-to-entitlements":
        return gameconfig_wrap([])
    return None


def handle_entitlements(path, body):
    if path == "/entitlements/list":
        with STATE_LOCK:
            return {"entitlementQuantities": copy.deepcopy(
                STATE["entitlements"])}
    if path == "/entitlements/visibility":
        wanted = set((body or {}).get("entitlementIds", []))
        with STATE_LOCK:
            found = [copy.deepcopy(e) for e in STATE["entitlements"]
                     if e["entitlementId"] in wanted]
        return {"entitlementQuantities": found}
    return None


def handle_offer(path, body):
    if path == "/offers/list":
        return D.offers_list()
    if path == "/getuserinfo":
        with STATE_LOCK:
            credits = STATE["credits"]
        return {"purchasingInfo": {"currency": {"credits": credits}}}
    if path == "/offers/buy":
        offer_id = (body or {}).get("offerId", "")
        with STATE_LOCK:
            offers = {o["offerId"]: o
                      for o in D.offers_list()["offers"]}
            offer = offers.get(offer_id)
            if not offer:
                return {"error": "unknown offer"}
            if offer_id == "battlepass_s1_pass":
                STATE["premium"] = True
                grant = [dict(e) for e in offer["entitlements"]]
                for e in grant:
                    for owned in STATE["entitlements"]:
                        if owned["entitlementId"] == e["entitlementId"]:
                            owned["amount"] = max(
                                owned.get("amount", 0), e.get("amount", 1))
                            break
                    else:
                        STATE["entitlements"].append(dict(e))
            else:
                for e in offer["entitlements"]:
                    for owned in STATE["entitlements"]:
                        if owned["entitlementId"] == e["entitlementId"]:
                            owned["amount"] += e.get("amount", 0)
                grant = offer["entitlements"]
            return {"success": True, "granted": grant,
                    "credits": STATE["credits"]}
    return None


def handle_identity(path, body):
    if path == "/players/getdisplayname":
        return {"displayName": "Hunter"}
    if path == "/players/getplayericon":
        return {"icon": "icon_0000_world_cruciblelogo"}
    if path == "/players/setdisplayname":
        return {"displayName": (body or {}).get("displayName", "Hunter")}
    if path == "/players/setplayericon":
        return {"icon": (body or {}).get("icon",
                                         "icon_0000_world_cruciblelogo")}
    return None


def handle_level(path, body):
    if path == "/configuration/levelxp":
        return {"levels": [{"level": i, "xp": i * 1000} for i in range(1, 41)]}
    if path in ("/characterxp", "/characterxp/"):
        return {"xp": []}
    if path == "/matchxp/":
        return {"xp": 0}
    return None


def handle_moderation(path, body):
    if path == "/createreport":
        return {"reportId": "local-report-1"}
    return None


LOCAL_PLAYER = "local-hunter"
LOCAL_PARTY = "local-party"


def handle_party(path, body):
    body = body or {}
    if path.startswith("/status/"):
        status = path[len("/status/"):]
        with STATE_LOCK:
            STATE["player_status"] = status
        return {"status": status, "partyId": STATE.get("party_id",
                                                       LOCAL_PARTY)}
    if path.startswith("/describeParty/"):
        pid = path[len("/describeParty/"):]
        return {"party": {
            "partyId": pid or LOCAL_PARTY,
            "partyMetaData": False,
            "PlayersRankedUnlocked": False,
            "Players": [{"playerId": body.get("playerId", LOCAL_PLAYER),
                         "status": STATE.get("player_status", "NOT_READY")}],
        }}
    if path.startswith("/getPartyForPlayer/"):
        return {"party": {
            "partyId": LOCAL_PARTY,
            "partyMetaData": False,
            "PlayersRankedUnlocked": False,
            "Players": [{"playerId": LOCAL_PLAYER,
                         "status": STATE.get("player_status", "NOT_READY")}],
        }}
    if path == "/leaveParty":
        with STATE_LOCK:
            STATE["party_id"] = LOCAL_PARTY
            STATE["player_status"] = "NOT_READY"
        return {"partyId": LOCAL_PARTY}
    if path.startswith("/kickPlayer/") or path.startswith("/promotePlayer/"):
        return {"success": True}
    if path.startswith("/gameRule/"):
        return {"gameRule": path[len("/gameRule/"):]}
    if path == "/customgames/startgame":
        return {"success": False, "error": "no servers in local mode"}
    if path == "/customgames/setplayerposition":
        return {"success": True}
    if path == "/customgames/swapplayerpositions":
        return {"success": True}
    return None


def handle_sysmsgs(path):
    if path == "/gamemodes.json":
        # The mode-select store reads `e.modes`; a payload without that key
        # throws and the whole mode list (PRACTICE included) renders empty.
        return D.gamemodes_payload()
    if path == "/motd.json":
        return {"messages": []}
    # /betaschedule.json is DELIBERATELY left unhandled (404). It looks like a
    # missing route - primary-modes.js polls it every 60s and logs an error each
    # time - but answering it BREAKS the mode-select screen.
    #
    # Why: the mode card's update does
    #     ({noticeText, scheduleText} = n)      // n = the beta-schedule value
    # and the store is fed `n[locale]`. A 404 leaves that store at its initial
    # `false`, and destructuring `false` is legal (both keys come out
    # undefined). Return a JSON object with no entry for the active locale and
    # the store becomes `undefined`, so the destructuring throws
    # "TypeError: Right side of assignment cannot be destructured" inside
    # Svelte's update - which soft-locks the UI: cards paint, clicks do nothing.
    #
    # Verified the hard way 2026-09-16. To handle this route safely the payload
    # would have to carry an entry for EVERY locale the client can request;
    # a partial map reintroduces the crash. The 404's log noise is cosmetic.
    return None


ROUTERS = [
    ("CrucibleLiveChallengeService", handle_challenges),
    ("CrucibleLiveGameConfigService", lambda p, b: handle_gameconfig(p)),
    ("CrucibleLiveEntitlementService", handle_entitlements),
    ("CrucibleLiveOfferService", handle_offer),
    ("CrucibleLivePlayerIdentityService", handle_identity),
    ("CrucibleLiveCharacterLevelService", handle_level),
    ("CrucibleLiveModerationService", handle_moderation),
    ("CrucibleLivePartyService", handle_party),
    ("CrucibleLiveSystemMessages", lambda p, b: handle_sysmsgs(p)),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "CrucibleRevival/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("backend %s %s\n" % (self.address_string(),
                                              fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _route(self, body):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/", 1)
        if len(parts) != 2:
            return None
        service, rest = parts
        for name, handler in ROUTERS:
            if name == service:
                return handler("/" + rest, body)
        return None

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        try:
            result = self._route(body)
        except Exception as exc:  # never hang the UI on a backend bug
            result = {"error": str(exc)}
        if result is None:
            self.send_response(404)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {"error": "unknown route", "path": self.path}
            self.wfile.write(json.dumps(payload).encode())
            return
        data = json.dumps(result).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _serve
    do_POST = _serve
    do_PUT = _serve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18876)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("crucible local backend on 127.0.0.1:%d" % args.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

