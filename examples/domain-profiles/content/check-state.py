"""A narrow structural check, not a judge of evidence or prose quality."""
import json
import sys
from pathlib import Path


def check(state):
    errors = []
    collections = {}
    fields = {
        "sources": ("id", "ref", "text"),
        "claims": ("id", "text", "kind", "source_ids"),
        "drafts": ("id", "path", "claim_ids"),
        "reviews": ("id", "draft_id", "status", "issues"),
    }
    for name, required in fields.items():
        rows = state.get(name)
        if not isinstance(rows, list):
            errors.append(f"{name}: expected an array")
            rows = []
        indexed = {}
        for index, row in enumerate(rows):
            label = f"{name}[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{label}: expected an object")
                continue
            for field in required:
                if field not in row:
                    errors.append(f"{label}: missing {field}")
            key = row.get("id")
            if not isinstance(key, str) or not key:
                errors.append(f"{label}: id must be a nonempty string")
                continue
            if key in indexed:
                errors.append(f"{label}: duplicate id {key}")
            indexed[key] = row
        collections[name] = indexed
    sources, claims, drafts, reviews = (collections[k] for k in fields)

    def refs(row, field, target):
        values = row.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            errors.append(f"{row['id']}: {field} must be an array of string IDs")
            return []
        for value in values:
            if value not in target:
                errors.append(f"{row['id']}: unknown {field} reference {value}")
        return values

    for claim in claims.values():
        ids = refs(claim, "source_ids", sources)
        if claim.get("kind") not in ("observation", "interpretation", "unsupported"):
            errors.append(f"{claim['id']}: invalid kind")
        elif claim["kind"] != "unsupported" and not ids:
            errors.append(f"{claim['id']}: supported claim has no source")
        if "quote" in claim:
            quote = claim["quote"]
            texts = [sources[i].get("text", "") for i in ids if i in sources]
            if not isinstance(quote, str) or not quote or not any(
                isinstance(text, str) and quote in text for text in texts
            ):
                errors.append(f"{claim['id']}: quote not found verbatim in cited excerpts")
    for draft in drafts.values():
        refs(draft, "claim_ids", claims)
    for review in reviews.values():
        if not isinstance(review.get("draft_id"), str) or review["draft_id"] not in drafts:
            errors.append(f"{review['id']}: unknown draft")
        if review.get("status") not in ("needs_work", "ready_for_user"):
            errors.append(f"{review['id']}: invalid status")
        if not isinstance(review.get("issues"), list):
            errors.append(f"{review['id']}: issues must be an array")
    return errors


if __name__ == "__main__":
    try:
        value = json.loads(Path(sys.argv[1]).read_text())
        if not isinstance(value, dict):
            raise ValueError("state must be an object")
        errors = check(value)
    except (OSError, ValueError, IndexError) as exc:
        errors = [str(exc)]
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
    sys.exit(bool(errors))
