# RiskSphere conversation evaluation: planner=live splits=dev
_2026-11-18T20:50:55. 34 conversations, 97 turns_

## Headline
| Metric | Value |
|---|---|
| Whole conversation passes (every turn right) | 85.3% (29/34, CI 70-94) |
| Turns that pass | 93.8% (91/97, CI 87-97) |
| Turns that pass, up to each conversation's first failure | 94.7% (90/95, CI 88-98) |
| Memory and pending question correct after the turn | 98.9% (91/92, CI 94-100) |

A conversation fails at its first wrong turn; later turns depend on that state, so the third row is the cleaner estimate of per-turn accuracy.

## Pass rate by category
| Category | Conversations passing |
|---|---|
| clarify_resolve | 100.0% (8/8, CI 68-100) |
| clarify_retry | 50.0% (2/4, CI 15-85) |
| fresh_session | 0.0% (0/2, CI 0-66) |
| interrupted_clarify | 66.7% (2/3, CI 21-94) |
| kill_switch | 100.0% (1/1, CI 21-100) |
| long_session | 100.0% (2/2, CI 34-100) |
| memory_neutral_turn | 100.0% (2/2, CI 34-100) |
| memory_no_pronoun | 100.0% (2/2, CI 34-100) |
| memory_pronoun | 100.0% (3/3, CI 44-100) |
| memory_switch | 100.0% (1/1, CI 21-100) |
| refusal_midway | 100.0% (6/6, CI 61-100) |

## Pass rate by requirement
| Requirement | Conversations passing |
|---|---|
| R01 | 100.0% (1/1, CI 21-100) |
| R02 | 100.0% (1/1, CI 21-100) |
| R03 | 100.0% (1/1, CI 21-100) |
| R04 | 100.0% (1/1, CI 21-100) |
| R05 | 100.0% (1/1, CI 21-100) |
| R06 | 100.0% (1/1, CI 21-100) |
| R13 | 100.0% (1/1, CI 21-100) |
| R14 | 100.0% (7/7, CI 65-100) |
| R15 | 100.0% (11/11, CI 74-100) |
| R30 | 84.6% (11/13, CI 58-96) |
| R31 | 50.0% (2/4, CI 15-85) |
| R32 | 66.7% (2/3, CI 21-94) |
| R33 | 100.0% (8/8, CI 68-100) |
| R34 | 0.0% (0/2, CI 0-66) |
| R35 | 100.0% (7/7, CI 65-100) |

## Failures (first failing turn)
| Id | Category | Turn | User said | Kind | Detail |
|---|---|---|---|---|---|
| CONV-10 | clarify_retry | 2 | I am not sure | turn_outcome | expected clarify, got refuse (planner) |
| CONV-11 | clarify_retry | 2 | Meridian | turn_outcome | expected clarify, got answer (['current_exposure']) |
| CONV-21 | interrupted_clarify | 2 | What is Apex's PFE? | turn_tools | expected ['check_pfe_limit'], got ['calculate_margin_call'] |
| CONV-33 | fresh_session | 2 | What is its PFE? | turn_outcome | expected clarify, got answer (['check_pfe_limit']) |
| CONV-34 | fresh_session | 2 | Apex | turn_tools | expected ['current_exposure'], got ['calculate_margin_call'] |
