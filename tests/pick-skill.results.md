
| # | prompt | expect | got | pass |
|---|---|---|---|---|
| 1 | my tests fail randomly on CI, look at it | pick-skill|systematic-debugging | NONE, systematic-debugging | 1/2 ❌ |
| 2 | what's the model id for the latest opus? | claude-api | claude-api, claude-api | 2/2 ✅ |
| 3 | rename foo to bar in utils.py | NONE | NONE, NONE | 2/2 ✅ |
| 4 | I want to build something that summarizes my standups | pick-skill|brainstorming | pick-skill, pick-skill | 2/2 ✅ |
| 5 | clean this up | pick-skill|simplify | pick-skill, pick-skill | 2/2 ✅ |
| 6 | what does git rebase --onto do | NONE | NONE, NONE | 2/2 ✅ |
| 7 | make a skill for our release checklist | pick-skill|writing-skills | NONE, NONE | 0/2 ❌ |
| 8 | I think my change is done, ship it | pick-skill|verification-before-completion | NONE, NONE | 0/2 ❌ |
| 9 | fix the auth bug, stack trace: KeyError 'token' at auth.py:42 | pick-skill|systematic-debugging | NONE, NONE | 0/2 ❌ |
| 10 | make it better | pick-skill | pick-skill, NONE | 1/2 ❌ |

Cases passing: 5/10  (min-pass 100% of 2 trials)

| skill | correct fires | false fires |
|---|---|---|
| claude-api | 2 | 0 |
| pick-skill | 5 | 0 |
| systematic-debugging | 1 | 0 |
