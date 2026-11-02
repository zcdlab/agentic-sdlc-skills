
| # | prompt | expect | got | pass |
|---|---|---|---|---|
| 1 | my tests fail randomly on CI, look at it | pick-skill|systematic-debugging | systematic-debugging, systematic-debugging | 2/2 ✅ |
| 2 | make a skill for our release checklist | pick-skill|writing-skills | NONE, NONE | 0/2 ❌ |
| 3 | I think my change is done, ship it | pick-skill|verification-before-completion | NONE, NONE | 0/2 ❌ |
| 4 | fix the auth bug, stack trace: KeyError 'token' at auth.py:42 | pick-skill|systematic-debugging | NONE, NONE | 0/2 ❌ |
| 5 | make it better | pick-skill | pick-skill, pick-skill | 2/2 ✅ |

Cases passing: 2/5  (min-pass 100% of 2 trials)

| fired | correct | wrong |
|---|---|---|
| NONE | 0 | 6 |
| pick-skill | 2 | 0 |
| systematic-debugging | 2 | 0 |
