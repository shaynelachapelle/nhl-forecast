### What
<!-- Bullet the changes: new modules, commands, migrations -->

### Why
<!-- The roadmap step or problem this addresses -->

### Results
<!-- For data/model changes: counts, validation output, metrics -->

### Checklist
- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run pytest` against `nhl_test` (run `scripts/reset_test_db.sh` after new migrations)
- [ ] New tables enable RLS in their migration
- [ ] Migrations not yet pushed to prod, or prod push planned
