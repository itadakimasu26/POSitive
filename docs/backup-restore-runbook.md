# Backup and restore runbook

Use this checklist before onboarding paying stores and repeat the restore exercise at least quarterly.

## 1. Confirm the managed database plan

In the production database provider's console, record the current:

- automated-backup schedule;
- retention period;
- point-in-time recovery availability and window;
- region and account owner; and
- most recent successful provider-managed restore test.

Do not publish a retention or recovery promise until those values are confirmed on the active production plan.

## 2. Create an independent logical backup

Run this from a trusted operator machine connected to the intended production `DATABASE_URL`. Save the output in encrypted storage outside both Vercel and the primary database provider.

```powershell
python manage.py backup_database --output D:\secure-backups\oxpos-YYYY-MM-DD.json.gz
```

The command exports OXPOS business records and user accounts, then writes a SHA-256 checksum beside the backup. Treat both files as sensitive because the export contains customer, staff, and sales data plus password hashes.

## 3. Verify that the export restores

```powershell
python manage.py verify_database_backup --input D:\secure-backups\oxpos-YYYY-MM-DD.json.gz
```

Verification checks the checksum, restores the fixture into a newly migrated temporary SQLite database, runs Django's database checks, and deletes the temporary database. It never writes into the configured production database.

## 4. Record the exercise

Record the operator, date, source database, backup checksum, verification result, elapsed time, and any corrective work. Keep at least one verified independent export according to the retention policy approved for the business.

The Vercel function filesystem is temporary and is not an acceptable backup destination.
