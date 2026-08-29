"""rename projects to products (Company -> Product -> Sub-product restructuring)

Revision ID: 4e9b89e80b3e
Revises: f407d3b76d34
Create Date: 2026-08-28 00:00:00.000000

Renames the `projects`/`project_members` tables and every `project_id` FK
column that points at them (brand_profiles, social_accounts, creative_briefs,
generation_jobs, audit_logs) to `products`/`product_members`/`product_id`,
and remaps ProjectRole's three old values to the five new ProductRole values
(project_admin -> product_manager, editor -> creator, viewer -> analyst).
Data-preserving throughout -- existing rows keep their id and FK linkage,
only names change.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4e9b89e80b3e'
down_revision: Union[str, Sequence[str], None] = 'f407d3b76d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- projects -> products -------------------------------------------------
    op.execute("ALTER TABLE `projects` DROP FOREIGN KEY `fk_projects_company_id_companies`")
    op.execute("ALTER TABLE `projects` DROP FOREIGN KEY `fk_projects_created_by_users`")
    op.execute("ALTER TABLE `projects` DROP CONSTRAINT `ck_projects_projectstatus`")
    op.execute("ALTER TABLE `projects` DROP INDEX `uq_projects_company_slug`")
    op.execute("ALTER TABLE `projects` DROP INDEX `ix_projects_company_id`")
    op.execute("ALTER TABLE `projects` DROP INDEX `fk_projects_created_by_users`")
    op.rename_table('projects', 'products')
    op.execute(
        "ALTER TABLE `products` "
        "ADD UNIQUE KEY `uq_products_company_slug` (`company_id`, `slug`), "
        "ADD INDEX `ix_products_company_id` (`company_id`), "
        "ADD CONSTRAINT `fk_products_company_id_companies` FOREIGN KEY (`company_id`) REFERENCES `companies` (`id`), "
        "ADD CONSTRAINT `fk_products_created_by_users` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`), "
        "ADD CONSTRAINT `ck_products_productstatus` CHECK (`status` in ('active','archived'))"
    )

    # --- project_members -> product_members -----------------------------------
    op.execute(
        "ALTER TABLE `project_members` DROP FOREIGN KEY `fk_project_members_project_id_projects`"
    )
    op.execute("ALTER TABLE `project_members` DROP CONSTRAINT `ck_project_members_projectrole`")
    op.execute("ALTER TABLE `project_members` DROP INDEX `uq_project_members_project_user`")
    op.execute("ALTER TABLE `project_members` DROP INDEX `ix_project_members_project_id`")
    op.execute(
        "UPDATE `project_members` SET `role` = CASE `role` "
        "WHEN 'project_admin' THEN 'product_manager' "
        "WHEN 'editor' THEN 'creator' "
        "WHEN 'viewer' THEN 'analyst' "
        "ELSE `role` END"
    )
    op.rename_table('project_members', 'product_members')
    op.execute(
        "ALTER TABLE `product_members` "
        "CHANGE COLUMN `project_id` `product_id` CHAR(36) NOT NULL, "
        "ADD COLUMN `sub_product_ids` LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin "
        "NOT NULL DEFAULT '[]' CHECK (json_valid(`sub_product_ids`))"
    )
    op.execute(
        "ALTER TABLE `product_members` "
        "ADD UNIQUE KEY `uq_product_members_product_user` (`product_id`, `user_id`), "
        "ADD INDEX `ix_product_members_product_id` (`product_id`), "
        "ADD CONSTRAINT `fk_product_members_product_id_products` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`), "
        "ADD CONSTRAINT `ck_product_members_productrole` CHECK "
        "(`role` in ('creator','approver','publisher','analyst','product_manager'))"
    )
    # Model has no server-side default for sub_product_ids (python-side
    # default=list only) -- drop it now that every existing row is backfilled.
    op.execute("ALTER TABLE `product_members` ALTER COLUMN `sub_product_ids` DROP DEFAULT")
    # user_id/added_by FKs never referenced `projects` and didn't need
    # touching above, but their auto-named supporting indexes/constraints
    # still said "project_members" -- rename those too for consistency.
    op.execute(
        "ALTER TABLE `product_members` DROP FOREIGN KEY `fk_project_members_added_by_users`"
    )
    op.execute(
        "ALTER TABLE `product_members` DROP FOREIGN KEY `fk_project_members_user_id_users`"
    )
    op.execute("ALTER TABLE `product_members` DROP INDEX `ix_project_members_user_id`")
    op.execute("ALTER TABLE `product_members` DROP INDEX `fk_project_members_added_by_users`")
    op.execute("ALTER TABLE `product_members` ADD INDEX `ix_product_members_user_id` (`user_id`)")
    op.execute(
        "ALTER TABLE `product_members` "
        "ADD CONSTRAINT `fk_product_members_added_by_users` FOREIGN KEY (`added_by`) REFERENCES `users` (`id`), "
        "ADD CONSTRAINT `fk_product_members_user_id_users` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)"
    )

    # --- brand_profiles.project_id -> product_id -------------------------------
    op.execute(
        "ALTER TABLE `brand_profiles` DROP FOREIGN KEY `fk_brand_profiles_project_id_projects`"
    )
    op.execute("ALTER TABLE `brand_profiles` DROP INDEX `uq_brand_profiles_project_id`")
    op.execute("ALTER TABLE `brand_profiles` DROP INDEX `ix_brand_profiles_project_id`")
    op.execute(
        "ALTER TABLE `brand_profiles` CHANGE COLUMN `project_id` `product_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `brand_profiles` "
        "ADD UNIQUE KEY `uq_brand_profiles_product_id` (`product_id`), "
        "ADD INDEX `ix_brand_profiles_product_id` (`product_id`), "
        "ADD CONSTRAINT `fk_brand_profiles_product_id_products` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)"
    )

    # --- social_accounts.project_id -> product_id -------------------------------
    op.execute(
        "ALTER TABLE `social_accounts` DROP FOREIGN KEY `fk_social_accounts_project_id_projects`"
    )
    op.execute(
        "ALTER TABLE `social_accounts` DROP INDEX `uq_social_accounts_project_platform_handle`"
    )
    op.execute("ALTER TABLE `social_accounts` DROP INDEX `ix_social_accounts_project_id`")
    op.execute(
        "ALTER TABLE `social_accounts` CHANGE COLUMN `project_id` `product_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `social_accounts` "
        "ADD UNIQUE KEY `uq_social_accounts_product_platform_handle` (`product_id`, `platform`, `handle`), "
        "ADD INDEX `ix_social_accounts_product_id` (`product_id`), "
        "ADD CONSTRAINT `fk_social_accounts_product_id_products` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)"
    )

    # --- creative_briefs.project_id -> product_id -------------------------------
    op.execute(
        "ALTER TABLE `creative_briefs` DROP FOREIGN KEY `fk_creative_briefs_project_id_projects`"
    )
    op.execute("ALTER TABLE `creative_briefs` DROP INDEX `ix_creative_briefs_project_id`")
    op.execute(
        "ALTER TABLE `creative_briefs` CHANGE COLUMN `project_id` `product_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `creative_briefs` "
        "ADD INDEX `ix_creative_briefs_product_id` (`product_id`), "
        "ADD CONSTRAINT `fk_creative_briefs_product_id_products` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)"
    )

    # --- generation_jobs.project_id -> product_id -------------------------------
    op.execute(
        "ALTER TABLE `generation_jobs` DROP FOREIGN KEY `fk_generation_jobs_project_id_projects`"
    )
    op.execute("ALTER TABLE `generation_jobs` DROP INDEX `ix_generation_jobs_project_id`")
    op.execute(
        "ALTER TABLE `generation_jobs` CHANGE COLUMN `project_id` `product_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `generation_jobs` "
        "ADD INDEX `ix_generation_jobs_product_id` (`product_id`), "
        "ADD CONSTRAINT `fk_generation_jobs_product_id_products` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)"
    )

    # --- audit_logs.project_id -> product_id ------------------------------------
    op.execute("ALTER TABLE `audit_logs` DROP FOREIGN KEY `fk_audit_logs_project_id_projects`")
    op.execute("ALTER TABLE `audit_logs` DROP INDEX `fk_audit_logs_project_id_projects`")
    op.execute(
        "ALTER TABLE `audit_logs` CHANGE COLUMN `project_id` `product_id` CHAR(36) NULL"
    )
    op.execute(
        "ALTER TABLE `audit_logs` "
        "ADD CONSTRAINT `fk_audit_logs_product_id_products` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE `audit_logs` DROP FOREIGN KEY `fk_audit_logs_product_id_products`")
    op.execute("ALTER TABLE `audit_logs` CHANGE COLUMN `product_id` `project_id` CHAR(36) NULL")
    op.execute(
        "ALTER TABLE `audit_logs` ADD INDEX `fk_audit_logs_project_id_projects` (`project_id`)"
    )

    op.execute(
        "ALTER TABLE `generation_jobs` DROP FOREIGN KEY `fk_generation_jobs_product_id_products`"
    )
    op.execute("ALTER TABLE `generation_jobs` DROP INDEX `ix_generation_jobs_product_id`")
    op.execute(
        "ALTER TABLE `generation_jobs` CHANGE COLUMN `product_id` `project_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `generation_jobs` "
        "ADD INDEX `ix_generation_jobs_project_id` (`project_id`), "
        "ADD CONSTRAINT `fk_generation_jobs_project_id_projects` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)"
    )

    op.execute(
        "ALTER TABLE `creative_briefs` DROP FOREIGN KEY `fk_creative_briefs_product_id_products`"
    )
    op.execute("ALTER TABLE `creative_briefs` DROP INDEX `ix_creative_briefs_product_id`")
    op.execute(
        "ALTER TABLE `creative_briefs` CHANGE COLUMN `product_id` `project_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `creative_briefs` "
        "ADD INDEX `ix_creative_briefs_project_id` (`project_id`), "
        "ADD CONSTRAINT `fk_creative_briefs_project_id_projects` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)"
    )

    op.execute(
        "ALTER TABLE `social_accounts` DROP FOREIGN KEY `fk_social_accounts_product_id_products`"
    )
    op.execute(
        "ALTER TABLE `social_accounts` DROP INDEX `uq_social_accounts_product_platform_handle`"
    )
    op.execute("ALTER TABLE `social_accounts` DROP INDEX `ix_social_accounts_product_id`")
    op.execute(
        "ALTER TABLE `social_accounts` CHANGE COLUMN `product_id` `project_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `social_accounts` "
        "ADD UNIQUE KEY `uq_social_accounts_project_platform_handle` (`project_id`, `platform`, `handle`), "
        "ADD INDEX `ix_social_accounts_project_id` (`project_id`), "
        "ADD CONSTRAINT `fk_social_accounts_project_id_projects` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)"
    )

    op.execute(
        "ALTER TABLE `brand_profiles` DROP FOREIGN KEY `fk_brand_profiles_product_id_products`"
    )
    op.execute("ALTER TABLE `brand_profiles` DROP INDEX `uq_brand_profiles_product_id`")
    op.execute("ALTER TABLE `brand_profiles` DROP INDEX `ix_brand_profiles_product_id`")
    op.execute(
        "ALTER TABLE `brand_profiles` CHANGE COLUMN `product_id` `project_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `brand_profiles` "
        "ADD UNIQUE KEY `uq_brand_profiles_project_id` (`project_id`), "
        "ADD INDEX `ix_brand_profiles_project_id` (`project_id`), "
        "ADD CONSTRAINT `fk_brand_profiles_project_id_projects` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)"
    )

    op.execute(
        "ALTER TABLE `product_members` DROP FOREIGN KEY `fk_product_members_added_by_users`"
    )
    op.execute(
        "ALTER TABLE `product_members` DROP FOREIGN KEY `fk_product_members_user_id_users`"
    )
    op.execute("ALTER TABLE `product_members` DROP INDEX `ix_product_members_user_id`")
    op.execute(
        "ALTER TABLE `product_members` "
        "ADD CONSTRAINT `fk_project_members_added_by_users` FOREIGN KEY (`added_by`) REFERENCES `users` (`id`), "
        "ADD CONSTRAINT `fk_project_members_user_id_users` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)"
    )
    op.execute(
        "ALTER TABLE `product_members` "
        "DROP FOREIGN KEY `fk_product_members_product_id_products`"
    )
    op.execute("ALTER TABLE `product_members` DROP CONSTRAINT `ck_product_members_productrole`")
    op.execute("ALTER TABLE `product_members` DROP INDEX `uq_product_members_product_user`")
    op.execute("ALTER TABLE `product_members` DROP INDEX `ix_product_members_product_id`")
    op.execute(
        "UPDATE `product_members` SET `role` = CASE `role` "
        "WHEN 'product_manager' THEN 'project_admin' "
        "WHEN 'creator' THEN 'editor' "
        "ELSE 'viewer' END"
    )
    op.execute("ALTER TABLE `product_members` DROP COLUMN `sub_product_ids`")
    op.rename_table('product_members', 'project_members')
    op.execute(
        "ALTER TABLE `project_members` CHANGE COLUMN `product_id` `project_id` CHAR(36) NOT NULL"
    )
    op.execute(
        "ALTER TABLE `project_members` "
        "ADD UNIQUE KEY `uq_project_members_project_user` (`project_id`, `user_id`), "
        "ADD INDEX `ix_project_members_project_id` (`project_id`), "
        "ADD CONSTRAINT `fk_project_members_project_id_projects` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`), "
        "ADD CONSTRAINT `ck_project_members_projectrole` CHECK (`role` in ('project_admin','editor','viewer'))"
    )

    op.execute("ALTER TABLE `products` DROP FOREIGN KEY `fk_products_company_id_companies`")
    op.execute("ALTER TABLE `products` DROP FOREIGN KEY `fk_products_created_by_users`")
    op.execute("ALTER TABLE `products` DROP CONSTRAINT `ck_products_productstatus`")
    op.execute("ALTER TABLE `products` DROP INDEX `uq_products_company_slug`")
    op.execute("ALTER TABLE `products` DROP INDEX `ix_products_company_id`")
    op.rename_table('products', 'projects')
    op.execute(
        "ALTER TABLE `projects` "
        "ADD UNIQUE KEY `uq_projects_company_slug` (`company_id`, `slug`), "
        "ADD INDEX `ix_projects_company_id` (`company_id`), "
        "ADD CONSTRAINT `fk_projects_company_id_companies` FOREIGN KEY (`company_id`) REFERENCES `companies` (`id`), "
        "ADD CONSTRAINT `fk_projects_created_by_users` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`), "
        "ADD CONSTRAINT `ck_projects_projectstatus` CHECK (`status` in ('active','archived'))"
    )
