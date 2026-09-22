"""Join the independently delivered resource-scope and Day-2 histories.

Both parent migrations are additive and operate on distinct schema additions.
Keep published migration identities unchanged and restore one upgrade head.
"""
revision = 'c864db917f20'
down_revision = ('b752ca806e19', '9d2c4e7a1b60')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
