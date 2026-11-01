---
name: sqlalchemy
when: [database]
keywords: [sqlalchemy, model, migration, schema]
---
# SQLAlchemy 2.x
- Declarative models with `Mapped[...]` and `mapped_column(...)`; explicit `__tablename__`.
- Every schema change ships with a migration (Alembic) that can be applied to existing data.
- Use `session.scalars(select(Model).where(...))`; no string SQL with user input.
- Constraints (nullable, unique, foreign keys) live in the model, not only in app code.
- Tests use an in-memory SQLite engine created per test.
```python
class Plant(Base):
    __tablename__ = "plants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
```
