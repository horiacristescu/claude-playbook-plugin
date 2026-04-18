# Monitor Rules

> Steering rules applied by the conversation monitor.
> Starts empty. Fills from observing user corrections and steering interventions.
> Each rule: pattern description, signal to watch for, response level.

(empty — fills from observation)

## Rule: "interactive" means observe, not wire

**Pattern:** When user says they want something "interactive" during a build/debug phase, they usually mean *let me watch it run now*, not *add an --interactive flag and explain the design*.

**Signal:** Agent responds to "interactive" with edits + explanations across 2+ spans without ever launching the thing for the user to see. User escalates with "you didn't listen" or similar.

**Response:** nudge — point at the gap between editing-about-it and running-it.
