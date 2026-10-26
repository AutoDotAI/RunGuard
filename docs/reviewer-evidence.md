# Reviewer evidence

The links below are independent QA comments in the public AutoDotAI issue tracker. Each comment records the tested commit and exact checks; earlier failed QA comments remain in the same issue history. No public host or live model-provider call is claimed.

| Issue | Independent QA | Issue | Independent QA |
| --- | --- | --- | --- |
| [#1](https://github.com/AutoDotAI/RunGuard/issues/1) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/1#issuecomment-6417180708) | [#2](https://github.com/AutoDotAI/RunGuard/issues/2) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/2#issuecomment-6417477563) |
| [#3](https://github.com/AutoDotAI/RunGuard/issues/3) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/3#issuecomment-6417755906) | [#4](https://github.com/AutoDotAI/RunGuard/issues/4) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/4#issuecomment-6418054167) |
| [#5](https://github.com/AutoDotAI/RunGuard/issues/5) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/5#issuecomment-6418393324) | [#6](https://github.com/AutoDotAI/RunGuard/issues/6) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/6#issuecomment-6418730524) |
| [#7](https://github.com/AutoDotAI/RunGuard/issues/7) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/7#issuecomment-6418955917) | [#8](https://github.com/AutoDotAI/RunGuard/issues/8) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/8#issuecomment-6419221293) |
| [#9](https://github.com/AutoDotAI/RunGuard/issues/9) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/9#issuecomment-6419518924) | [#10](https://github.com/AutoDotAI/RunGuard/issues/10) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/10#issuecomment-6419999279) |
| [#11](https://github.com/AutoDotAI/RunGuard/issues/11) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/11#issuecomment-6420327594) | [#12](https://github.com/AutoDotAI/RunGuard/issues/12) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/12#issuecomment-6420683314) |
| [#13](https://github.com/AutoDotAI/RunGuard/issues/13) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/13#issuecomment-6421005824) | [#14](https://github.com/AutoDotAI/RunGuard/issues/14) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/14#issuecomment-6421228548) |
| [#15](https://github.com/AutoDotAI/RunGuard/issues/15) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/15#issuecomment-6421459774) | [#16](https://github.com/AutoDotAI/RunGuard/issues/16) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/16#issuecomment-6421761112) |
| [#17](https://github.com/AutoDotAI/RunGuard/issues/17) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/17#issuecomment-6422730854) | [#18](https://github.com/AutoDotAI/RunGuard/issues/18) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/18#issuecomment-6422967453) |
| [#19](https://github.com/AutoDotAI/RunGuard/issues/19) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/19#issuecomment-6423259092) | [#20](https://github.com/AutoDotAI/RunGuard/issues/20) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/20#issuecomment-6423498580) |
| [#21](https://github.com/AutoDotAI/RunGuard/issues/21) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/21#issuecomment-6423948518) | [#22](https://github.com/AutoDotAI/RunGuard/issues/22) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/22#issuecomment-6424152247) |
| [#23](https://github.com/AutoDotAI/RunGuard/issues/23) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/23#issuecomment-6425882382) | [#24](https://github.com/AutoDotAI/RunGuard/issues/24) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/24#issuecomment-6426103114) |
| [#25](https://github.com/AutoDotAI/RunGuard/issues/25) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/25#issuecomment-6425828224) | [#26](https://github.com/AutoDotAI/RunGuard/issues/26) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/26#issuecomment-6427064115) |
| [#27](https://github.com/AutoDotAI/RunGuard/issues/27) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/27#issuecomment-6427561751) | [#28](https://github.com/AutoDotAI/RunGuard/issues/28) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/28#issuecomment-6428037497) |
| [#29](https://github.com/AutoDotAI/RunGuard/issues/29) | [PASS](https://github.com/AutoDotAI/RunGuard/issues/29#issuecomment-6428628334) | | |

## CI and runtime evidence

- [CI baseline pass for #22](https://github.com/AutoDotAI/RunGuard/actions/runs/40377438270) and [deliberately failing unmerged PR #33](https://github.com/AutoDotAI/RunGuard/pull/33) verify pass/fail status.
- [CD digest-publication run for #24](https://github.com/AutoDotAI/RunGuard/actions/runs/40391573401) passed; its host deploy was skipped because no host is configured.
- [Local rollback QA for #25](https://github.com/AutoDotAI/RunGuard/issues/25#issuecomment-6425828224) covers a private A→B→A rehearsal.
- [Observability QA for #26](https://github.com/AutoDotAI/RunGuard/issues/26#issuecomment-6427064115), [alert QA for #27](https://github.com/AutoDotAI/RunGuard/issues/27#issuecomment-6427561751), and [controlled-trigger QA for #28](https://github.com/AutoDotAI/RunGuard/issues/28#issuecomment-6428037497) include isolated live Compose probes.
- [Main Actions workflow](https://github.com/AutoDotAI/RunGuard/actions/workflows/ci.yml) is the current status source.

The absence of a server/domain means public HTTPS, actual-host egress, CD, and rollback remain [#34](https://github.com/AutoDotAI/RunGuard/issues/34).
