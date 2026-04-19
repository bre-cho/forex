# Database Migrations

Alembic is used for all database migrations.

## Commands

```bash
# Create a new migration
cd apps/api && alembic revision --autogenerate -m "description"

# Apply migrations
cd apps/api && alembic upgrade head

# Rollback one step
cd apps/api && alembic downgrade -1

# Show current revision
cd apps/api && alembic current

# Show history
cd apps/api && alembic history
```

## Convention

- Migration files live in `apps/api/alembic/versions/`
- Always review auto-generated migrations before applying
- Never edit applied migrations; create a new one instead
