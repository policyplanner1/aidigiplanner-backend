"""Shared Jinja2 template loading for prompts/ -- ported unchanged from the
prototype's app/prompts.py.

A template's filename stem *is* its prompt version -- prompts/ideate_v1.j2
is version "ideate_v1". A job records that string, which both identifies
the template used and lets prompt changes be A/B'd (a new version is just a
new file; the old one keeps working for in-flight jobs).
"""

from __future__ import annotations

from functools import lru_cache

from jinja2 import Environment, FileSystemLoader, StrictUndefined


@lru_cache
def _env(prompts_dir: str) -> Environment:
    return Environment(
        loader=FileSystemLoader(prompts_dir),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )


def render_prompt(prompts_dir: str, template_stem: str, **context: object) -> str:
    template = _env(prompts_dir).get_template(f"{template_stem}.j2")
    return template.render(**context)
