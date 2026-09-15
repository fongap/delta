from pathlib import Path

path = Path("core/runtime-native/src/model_authority.rs")
text = path.read_text(encoding="utf-8")
old = "let profile = self.resolved_profile(&descriptor.name, prefs);"
new = "let profile = self.resolved_profile(&descriptor.name, prefs)?;"
count = text.count(old)
if count != 1:
    raise SystemExit(f"provider_row profile propagation: expected one match, got {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
