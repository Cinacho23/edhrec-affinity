# November 14 bounded browser rate test

[Diagnostic run and receipts](https://github.com/Cinacho23/edhrec-affinity/actions/runs/43604733040)
at commit `272a16e807369487129e2e3fa94627d14a6a85f8`.

The user requested 1.0–5.0 requests/second in 0.1 increments. The isolated
diagnostic used one standard GitHub-hosted WebKit VM, four browser contexts
sharing one pacing gate, and unique previews from The Tenth Doctor / Rose Tyler
gallery. Planned stages lasted 30 seconds each, with two minutes at 5.0 if
reached. Production settings and checkpoints were unchanged.

The experiment stopped on its first unexpected redirect. Saved browser events
show a **302 redirect to `/rate-limit`** at 14:30:35 UTC during the 1.9 stage.
There was no observed 429 response. The generic redirect guard called the run
`inconclusive`; the recorded destination provides direct rate-limit evidence.
No requests were started after that stop, no retry was made, and stages
2.0–5.0 were not attempted.

| Target requests/s | Valid previews | Measured starts/s | Completed |
|---:|---:|---:|:---|
| 1.0 | 30 | 1.000 | Yes |
| 1.1 | 33 | 1.100 | Yes |
| 1.2 | 36 | 1.200 | Yes |
| 1.3 | 39 | 1.300 | Yes |
| 1.4 | 42 | 1.400 | Yes |
| 1.5 | 45 | 1.500 | Yes |
| 1.6 | 48 | 1.600 | Yes |
| 1.7 | 51 | 1.700 | Yes |
| 1.8 | 54 | 1.800 | Yes |
| 1.9 | 2 | 1.899 | No |

There were 382 main document requests: one valid gallery, 380 valid previews,
and the redirected preview. Three waiting calls were cancelled without sending
documents. The global minimum recorded request-start gap was 0.526441 seconds,
consistent with the 1.9 ceiling. The rate-proof artifact's GitHub SHA256 is
`d3281664352a6e7cab6f4b7d5b8b965acaddf3742717dbee9e5832ff546e15e6`.

This establishes an observed stop during a 1.9 stage, not a permanent 1.9-RPS
quota or a proven sustainable 1.8-RPS budget. Each stage was short, earlier
requests may contribute to a rolling allowance, and other collection machines
have their own workload. Production stays at one request/second per VM.

The same workflow's separate source-free job inspected the immutable inputs of
production generation `43594279602`, created 12:31:24 UTC (7:31 AM Eastern).
The Tenth Doctor / Rose Tyler was first in worker 13's queue and held local
claim 0, alongside Sephiroth, the Savior on claim 1. It had 1,928 current
previews out of a 5,631-preview source frame, 3,703 remaining, no unavailable
previews, and no completed analysis. Its report was not yet published. This
snapshot precedes that generation's source work; it is not a live count.

Validation: 89 focused diagnostic/transport/policy tests and the complete
1,907-test Python suite passed. No production code was modified.
