# Plan: Consolidate Cohort Repos into Single Source of Truth

## Problem

Three repos maintaining the same Python codebase:
- **cohort-legacy** (private) — original monolith, where `G:\cohort` actually pushes
- **cohort** (public) — hand-curated subset for open source + PyPI publishing
- **cohort-vscode** — VS Code extension (separate, stays as-is)

This causes: version drift, pushing to wrong remotes, lint fixes applied to one but not the other, accidental exposure of private files, and a full day lost debugging install issues that cascaded from the split.

## Solution

One private repo is the single source of truth. A GitHub Action automatically syncs allowed files to the public repo on every push to `main` or version tag.

---

## Step 1: Rename cohort-legacy → cohort-private

On GitHub:
```
Settings → General → Repository name → cohort-private
```

Update the local remote:
```bash
cd G:\cohort
git remote set-url origin https://github.com/rwheeler007/cohort-private.git
```

## Step 2: Create the public file manifest

Create `G:\cohort/.public-files` — an explicit allowlist of what goes to the public repo. This is safer than a blocklist because new private files are excluded by default.

```
# .public-files — only these paths sync to rwheeler007/cohort
# Lines are rsync-style include patterns

# Root files
.github/workflows/ci.yml
.gitignore
CHANGELOG.md
CLAUDE.md
CODE_OF_CONDUCT.md
CONTRIBUTING.md
Dockerfile
LICENSE
NOTICE
QUICKSTART.md
README.md
SECURITY.md
docker-compose.yml
pyproject.toml

# Core package
cohort/**
!cohort/website/**

# Public extras
examples/**
plugins/**
services/**
tests/**
```

### Special mapping: Website → docs/

The website lives locally at `cohort/website/cohort/` but is served from `docs/` on the public repo (GitHub Pages). The sync workflow must copy `cohort/website/cohort/*` → `docs/` in the public repo. This is a path mapping, not a straight copy.

Old local website files are archived at `cohort/website/cohort/_archive/` — do not sync these.

The `docs/` directory should NOT be listed in `.public-files` directly since it doesn't exist locally — it's generated from the website path mapping.

**IMPORTANT**: Review this list carefully. In particular:
- `cohort/website/` — is this proprietary? If so, keep the exclusion
- `services/` — the public repo already has this, but confirm `services/comms_service/` and `services/wq_worker/` should be public
- `tests/` — make sure no test fixtures contain secrets or private data

## Step 3: Create the sync workflow in the PRIVATE repo

Create `.github/workflows/sync-public.yml`:

```yaml
name: Sync to Public Repo

on:
  push:
    branches: [main]
    tags: ["v*"]

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout private repo
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Checkout public repo
        uses: actions/checkout@v4
        with:
          repository: rwheeler007/cohort
          token: ${{ secrets.PUBLIC_REPO_TOKEN }}
          path: _public
          fetch-depth: 0

      - name: Sync allowed files
        run: |
          cd _public
          # Remove all tracked files (we'll repopulate from manifest)
          git rm -rf . --quiet 2>/dev/null || true
          cd ..

          # Copy files from manifest
          while IFS= read -r pattern; do
            # Skip comments and blank lines
            [[ "$pattern" =~ ^#.*$ || -z "$pattern" ]] && continue
            # Skip exclusion patterns (handled separately)
            [[ "$pattern" =~ ^!.*$ ]] && continue
            # Use rsync for glob support
            rsync -a --include="$pattern" --exclude="*" . _public/ 2>/dev/null || \
              cp -r $pattern _public/ 2>/dev/null || true
          done < .public-files

          # Apply exclusions
          while IFS= read -r pattern; do
            [[ "$pattern" =~ ^!(.*)$ ]] || continue
            excluded="${BASH_REMATCH[1]}"
            rm -rf "_public/$excluded" 2>/dev/null || true
          done < .public-files

      - name: Commit and push
        run: |
          cd _public
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -A

          if git diff --cached --quiet; then
            echo "No changes to sync"
            exit 0
          fi

          # Use the private repo's latest commit message
          COMMIT_MSG=$(git -C .. log -1 --format="%s")
          git commit -m "$COMMIT_MSG" -m "Auto-synced from private repo"
          git push origin main

      - name: Sync tag
        if: startsWith(github.ref, 'refs/tags/')
        run: |
          cd _public
          TAG_NAME="${GITHUB_REF#refs/tags/}"
          git tag "$TAG_NAME"
          git push origin "$TAG_NAME"
```

## Step 4: Set up the GitHub secret

1. Create a Personal Access Token (PAT) with `repo` scope at https://github.com/settings/tokens
2. Add it as a secret named `PUBLIC_REPO_TOKEN` in the **private** repo:
   Settings → Secrets and variables → Actions → New repository secret

## Step 5: Fix the lint and publish

Once the sync workflow is in place:

1. Fix all ruff lint errors in the private repo (already done locally — 178 files changed)
2. Commit and push to private repo's `main`
3. The sync workflow copies only public files to `rwheeler007/cohort`
4. Tag with `v0.4.15` on the private repo
5. Tag propagates to public repo → triggers CI → publishes to PyPI

## Step 6: Clean up

- Archive `cohort-legacy` if it was renamed, or delete if it was the same repo
- Remove any local `.pypirc` or manual publish scripts
- Update `cohort-vscode` if it references the cohort repo URL anywhere
- Update CLAUDE.md in both repos to document the new workflow

## Step 7: Update cohort-vscode remote

The VS Code extension repo is fine as a separate repo — it's a different codebase (TypeScript). No changes needed there except:
- Make sure `package.json` references the correct `cohort` public repo URL
- Version bumps should still be coordinated manually (or via a script)

---

## Workflow after consolidation

```
Developer makes changes in G:\cohort
  → git push origin main          (pushes to cohort-private)
  → GitHub Action fires           (syncs public files to cohort)
  → git tag v0.5.0 && git push --tags
  → Tag syncs to public repo      (triggers PyPI publish)
```

One push, everything updates. No manual copying, no version drift, no accidental leaks.

---

## Files that need private review before first sync

These files exist locally but may contain secrets or proprietary content. Review before adding to `.public-files`:

- [ ] `cohort/website/` — proprietary site content?
- [ ] `services/comms_service/` — contains email/calendar/discord integrations
- [ ] `services/wq_worker/` — the work queue dispatcher
- [ ] `plugins/cohort-channel/` — channel plugin with logs
- [ ] `tests/` — check for hardcoded URLs, tokens, or private test data
- [ ] `docs/launch-kit/` — marketing materials, probably private
- [ ] `CLAUDE.md` — contains internal architecture details

---

## Risk mitigation

- **Allowlist, not blocklist** — new files are private by default
- **Sync is one-way** — public repo is read-only (no PRs from public flow back)
- **Tag-based PyPI publish** — only explicit version tags trigger releases
- **PAT is scoped** — the token only needs `repo` access to `rwheeler007/cohort`
