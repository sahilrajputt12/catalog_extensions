# Step-by-Step Guide: Installing Catalog Extensions on Another Bench

## OVERVIEW
This guide shows you how to deploy `catalog_extensions` to a completely different bench (server or local instance) with minimal human intervention using the automated deployment scripts.

---

## PREREQUISITES

**On the target bench, you must have:**
- [ ] Frappe Bench installed and working
- [ ] Site created (e.g., `catalog2.local`)
- [ ] Dependencies installed: `payments`, `erpnext`, `webshop`
- [ ] SSH/terminal access to the bench server

---

## METHOD 1: Direct Copy (Recommended for Same Server)

### Step 1: Copy App to New Bench

```bash
# From source bench (where catalog_extensions is already)
cp -r /path/to/source_bench/apps/catalog_extensions /path/to/target_bench/apps/
```

### Step 2: Run Automated Deployment

```bash
# Switch to target bench
cd /path/to/target_bench

# Run full deployment (ONE COMMAND!)
bash apps/catalog_extensions/deploy/full_deploy.sh --site catalog2.local --restart
```

**That's it!** The script handles everything else automatically.

---

## METHOD 2: Git Clone (Recommended for Different Servers)

### Step 1: Clone the App Repository

If `catalog_extensions` is in a git repository:

```bash
cd /path/to/target_bench
cd apps

# Clone the repository
git clone https://github.com/yourusername/catalog_extensions.git
# OR
git clone /path/to/local/catalog_extensions.git

# Go back to bench root
cd ..
```

If it's NOT in git yet, initialize it on the source bench first:

```bash
# On SOURCE bench
cd apps/catalog_extensions
git init
git add .
git commit -m "Initial commit"
```

Then clone from the local path on target bench:

```bash
# On TARGET bench
cd apps
git clone /path/to/source_bench/apps/catalog_extensions.git
cd ..
```

### Step 2: Run Automated Deployment

```bash
cd /path/to/target_bench

# Install dependencies first (if not already installed)
bench get-app payments
bench get-app erpnext
bench get-app webshop

# Install webshop on the site
bench --site catalog2.local install-app webshop

# Run the automated deployment
bash apps/catalog_extensions/deploy/full_deploy.sh --site catalog2.local --restart
```

---

## METHOD 3: Manual Step-by-Step (For Learning/Control)

If you want to understand each step manually:

### Step 1: Copy App Files

```bash
# Copy the catalog_extensions folder
cp -r /source/bench/apps/catalog_extensions /target/bench/apps/
```

### Step 2: Install App on Site

```bash
cd /target/bench

# Install the app
bench --site catalog2.local install-app catalog_extensions
```

### Step 3: Run Migration

```bash
bench --site catalog2.local migrate
```

### Step 4: Create DocTypes

```bash
python3 apps/catalog_extensions/deploy/setup_doctypes.py --site catalog2.local
```

### Step 5: Create Custom Fields

```bash
python3 apps/catalog_extensions/deploy/setup_custom_fields.py --site catalog2.local
```

### Step 6: Restart and Clear Cache

```bash
bench --site catalog2.local clear-cache
bench restart
```

---

## METHOD 4: ZIP Archive (For Air-Gapped/Offline Systems)

### Step 1: Create Archive on Source

```bash
cd /path/to/source_bench/apps
tar -czvf catalog_extensions.tar.gz catalog_extensions/
```

### Step 2: Transfer to Target

```bash
# Option A: SCP
scp catalog_extensions.tar.gz user@target-server:/path/to/target_bench/apps/

# Option B: USB drive, shared folder, etc.
cp catalog_extensions.tar.gz /mnt/shared/
```

### Step 3: Extract and Deploy

```bash
# On target bench
cd /path/to/target_bench/apps
tar -xzvf catalog_extensions.tar.gz
cd ..

# Run automated deployment
bash apps/catalog_extensions/deploy/full_deploy.sh --site catalog2.local --restart
```

---

## VERIFICATION CHECKLIST

After deployment, verify:

```bash
cd /path/to/target_bench

# 1. App is installed
bench --site catalog2.local list-apps | grep catalog_extensions

# 2. DocType exists
bench --site catalog2.local mariadb -e "SELECT name FROM tabDocType WHERE name='Catalog Price Range'"

# 3. API is accessible
curl http://catalog2.local:8000/api/method/catalog_extensions.api.get_filter_facets

# 4. Check Desk
# Login to Desk, go to "Catalog Extensions" module, verify "Catalog Price Range" list
```

---

## TROUBLESHOOTING NEW BENCH

### "App not found"
The app must be in `apps/catalog_extensions/` before running deploy script.

### "Site does not exist"
Create the site first:
```bash
bench new-site catalog2.local
```

### "Webshop not installed"
Install dependencies in order:
```bash
bench get-app payments
bench get-app erpnext
bench get-app webshop
bench --site catalog2.local install-app webshop
```

### "Permission denied on scripts"
```bash
chmod +x apps/catalog_extensions/deploy/*.sh
chmod +x apps/catalog_extensions/deploy/*.py
```

### "Python3 not found"
Use `python` instead of `python3` or specify full path:
```bash
/path/to/python apps/catalog_extensions/deploy/setup_doctypes.py --site catalog2.local
```

---

## ONE-LINER FULL SETUP (Advanced)

For complete automation from a fresh bench:

```bash
#!/bin/bash
# setup_new_bench.sh - Run this on the TARGET bench

BENCH_PATH="/home/frappe/frappe-bench"
SITE_NAME="catalog2.local"
APP_SOURCE="/path/to/source/catalog_extensions"

set -e
cd $BENCH_PATH

# Step 1: Install dependencies
bench get-app payments
bench get-app erpnext
bench get-app webshop

# Step 2: Create site (if needed)
if [ ! -d "sites/$SITE_NAME" ]; then
    bench new-site $SITE_NAME
fi

# Step 3: Install webshop
bench --site $SITE_NAME install-app webshop || true

# Step 4: Copy app
cp -r $APP_SOURCE apps/catalog_extensions

# Step 5: Full automated deployment
bash apps/catalog_extensions/deploy/full_deploy.sh --site $SITE_NAME --restart

echo "✅ Deployment complete to $SITE_NAME"
```

Run it:
```bash
chmod +x setup_new_bench.sh
./setup_new_bench.sh
```

---

## SUMMARY

| Step | Action | Command |
|------|--------|---------|
| 1 | Get app to target | `cp -r` / `git clone` / `scp` |
| 2 | Install dependencies | `bench get-app payments erpnext webshop` |
| 3 | Run deployment | `bash deploy/full_deploy.sh --site SITE --restart` |
| 4 | Verify | `bench --site SITE list-apps` |

**The automated script handles everything else!**

---

## SUPPORT

If deployment fails:
1. Check `sites/SITE/logs/` for error logs
2. Verify all prerequisites are installed
3. Run individual scripts with `--help` for options
4. Check that you're in the correct bench directory
