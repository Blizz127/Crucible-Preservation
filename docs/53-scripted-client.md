# Testing a match from two participants

Run two independent simulated participants against one local server. Each records its own observations. Have one participant act and check what the other receives. Repeat in the opposite direction. A test that inspects only the server's internal state can miss a change sent to the wrong recipient.

Check that each participant controls its own character and can observe the other character's actions. At the end of the scenario, both participants should receive the same result. Identify each observation by participant when reporting a failure.

Keep automated assertions separate from observations made during a manual run. A passing network test establishes what the simulated participants received. Presentation and player controls require a separate rendered test.
