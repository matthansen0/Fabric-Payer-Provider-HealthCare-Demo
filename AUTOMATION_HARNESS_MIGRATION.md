# AUTOMATION HARNESS MIGRATION

## Summary

As of May 15, 2026, the automation and orchestration work developed in this fork has been **extracted into a separate repository** for cleaner separation of concerns.

## New Repository

**📍 Automation Harness Repo:** https://github.com/matthansen0/hls-iq-auto-harness

This new repo contains:
- ✅ All orchestration scripts (`scripts/automation/`, `scripts/azd/`)
- ✅ AzD configuration and provisioning hooks
- ✅ Dev container setup
- ✅ Deployment documentation (`AZD_AUTOMATION_GUIDE.md`)
- ✅ Main demo as a **git submodule** (no duplication)

## Why This Structure?

**Separation of Concerns:**
- **This fork** (`Fabric-Payer-Provider-HealthCare-Demo`) = The demo application and healthcare content
- **New harness** (`hls-iq-auto-harness`) = Orchestration, automation, and deployment machinery

**Benefits:**
- Cleaner git history for each repo
- Easier to track what changed where
- Harness can evolve independently of demo
- Main demo content stays in original repo

## What Stays Here?

All original demo content remains in this fork:
- Healthcare knowledge graphs and data
- Fabric workspace structure and notebooks
- Demo scenarios and runbooks
- Original configuration

This fork will continue to track updates from the **main repo** (`rasgiza/Fabric-Payer-Provider-HealthCare-Demo`).

## Workflow Going Forward

### If you're working on the **automation harness:**
```bash
git clone https://github.com/matthansen0/hls-iq-auto-harness.git
```

### If you're working on the **demo content:**
```bash
# Use this fork as-is, or sync with the main repo
git fetch upstream main
git rebase upstream/main
```

### To get both:
```bash
# Clone the harness (includes main demo as submodule)
git clone --recursive https://github.com/matthansen0/hls-iq-auto-harness.git

# This gives you:
# - hls-iq-auto-harness/       (automation code)
# - hls-iq-auto-harness/fabric-main/ (full demo via submodule)
```

## Updated Branches

This fork's `main` branch has been kept for **reference and sync purposes** with the original main repo. No new automation work is committed here.

All new automation development goes to: **https://github.com/matthansen0/hls-iq-auto-harness**

---

**Questions?** See the harness repo's [README](https://github.com/matthansen0/hls-iq-auto-harness/blob/main/README.md) and [CONTRIBUTING.md](https://github.com/matthansen0/hls-iq-auto-harness/blob/main/CONTRIBUTING.md).
