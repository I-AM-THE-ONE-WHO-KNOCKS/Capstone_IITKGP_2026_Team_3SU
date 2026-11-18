# RiskSphere conversation evaluation: planner=live splits=dev
_2026-11-18T00:35:30. 34 conversations, 97 turns_

## Headline
| Metric | Value |
|---|---|
| Whole conversation passes (every turn right) | 76.5% (26/34, CI 60-88) |
| Turns that pass | 90.7% (88/97, CI 83-95) |
| Turns that pass, up to each conversation's first failure | 91.1% (82/90, CI 83-95) |
| Memory and pending question correct after the turn | 98.9% (88/89, CI 94-100) |

A conversation fails at its first wrong turn; later turns depend on that state, so the third row is the cleaner estimate of per-turn accuracy.

## Pass rate by category
| Category | Conversations passing |
|---|---|
| clarify_resolve | 100.0% (8/8, CI 68-100) |
| clarify_retry | 50.0% (2/4, CI 15-85) |
| fresh_session | 0.0% (0/2, CI 0-66) |
| interrupted_clarify | 66.7% (2/3, CI 21-94) |
| kill_switch | 100.0% (1/1, CI 21-100) |
| long_session | 50.0% (1/2, CI 9-91) |
| memory_neutral_turn | 50.0% (1/2, CI 9-91) |
| memory_no_pronoun | 100.0% (2/2, CI 34-100) |
| memory_pronoun | 100.0% (3/3, CI 44-100) |
| memory_switch | 100.0% (1/1, CI 21-100) |
| refusal_midway | 83.3% (5/6, CI 44-97) |

## Pass rate by requirement
| Requirement | Conversations passing |
|---|---|
| R01 | 100.0% (1/1, CI 21-100) |
| R02 | 100.0% (1/1, CI 21-100) |
| R03 | 100.0% (1/1, CI 21-100) |
| R04 | 0.0% (0/1, CI 0-79) |
| R05 | 100.0% (1/1, CI 21-100) |
| R06 | 100.0% (1/1, CI 21-100) |
| R13 | 100.0% (1/1, CI 21-100) |
| R14 | 85.7% (6/7, CI 49-97) |
| R15 | 100.0% (11/11, CI 74-100) |
| R30 | 76.9% (10/13, CI 50-92) |
| R31 | 50.0% (2/4, CI 15-85) |
| R32 | 66.7% (2/3, CI 21-94) |
| R33 | 87.5% (7/8, CI 53-98) |
| R34 | 0.0% (0/2, CI 0-66) |
| R35 | 71.4% (5/7, CI 36-92) |

## Failures (first failing turn)
| Id | Category | Turn | User said | Kind | Detail |
|---|---|---|---|---|---|
| CONV-10 | clarify_retry | 2 | I am not sure | turn_outcome | expected clarify, got refuse (planner) |
| CONV-11 | clarify_retry | 2 | Meridian | turn_outcome | expected clarify, got answer (['current_exposure']) |
| CONV-16 | memory_neutral_turn | 1 | Is Harbor Lane through its limit? | turn_tools | expected ['current_exposure'], got ['check_pfe_limit'] |
| CONV-21 | interrupted_clarify | 2 | What is Apex's PFE? | turn_tools | expected ['check_pfe_limit'], got ['calculate_margin_call'] |
| CONV-25 | refusal_midway | 1 | Is Harbor Lane through its limit? | turn_tools | expected ['current_exposure'], got ['check_pfe_limit'] |
| CONV-31 | long_session | 8 | Is Harbor Lane through its limit? | turn_tools | expected ['current_exposure'], got ['check_pfe_limit'] |
| CONV-33 | fresh_session | 2 | What is its PFE? | turn_outcome | expected clarify, got answer (['check_pfe_limit']) |
| CONV-34 | fresh_session | 2 | Apex | turn_tools | expected ['current_exposure'], got ['calculate_margin_call'] |
