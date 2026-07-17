# Production Readiness Validator

The Production Readiness Validator acts as a gatekeeper, analyzing the system across 9 sections to produce an overall production readiness score.

## Section Weights & Checks

| Section | Weight (%) | Description / Checks |
|---|---|---|
| **Configuration** | 15 | Verifies yaml files are present, valid, and contain required project declarations |
| **Logging** | 10 | Verifies `logs/` directory exists and checks for active structured logger configuration |
| **Persistence** | 15 | Verifies `task_checkpoints.db` exists, tables exist, and has write access |
| **Recovery** | 15 | Verifies `BackupManager` is importable, backup directory exists, and backups are active |
| **Observability**| 10 | Verifies metrics and health modules are present and responding |
| **Git** | 10 | Checks Git CLI availability and verify local git workspaces are set up |
| **Security** | 10 | Verifies `BRIDGE_TOKEN` env var is present, check `.env` is ignored by Git, and checks that SQLite files are not committed |
| **Testing** | 10 | Verifies `tests/` directory exists and contains unit tests |
| **Documentation**| 5 | Checks for `README.md` and `CHANGELOG.md` files |

## Scoring Formula

- Each check inside a section outputs a severity: `PASS` (1.0 points), `WARNING` (0.5 points), or `FAIL` (0.0 points).
- **Section Score**: `(Sum of check scores) / (Total checks in section) * 100` (rounded to nearest integer).
- **Overall Score**: Weighted average of all section scores based on their relative weights:
  $$\text{Overall Score} = \frac{\sum (\text{Section Score} \times \text{Weight})}{\sum \text{Weights}}$$
- **Overall Status**:
  - `PASS`: Score $\ge 90\%$
  - `WARNING`: Score $70\% - 89\%$
  - `FAIL`: Score $< 70\%$
