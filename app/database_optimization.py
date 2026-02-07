"""
Database optimization for MongoDB.

MongoDB indexes are defined in the Beanie Document models via the
Settings.indexes class attribute or Indexed() field annotations.
This file is kept for backward compatibility but the SQLite/SQLAlchemy
index creation logic has been removed.
"""


def create_performance_indexes():
    """
    No-op. MongoDB indexes are managed by Beanie via Document model Settings.
    """
    pass
