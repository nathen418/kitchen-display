"""Extract a schema.org Recipe (JSON-LD, falling back to microdata) from an HTML page."""
import html as htmlmod
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}
DUR_RE =re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:[\d.]+S)?)?$", re.I)


def clean(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = htmlmod.unescape(TAG_RE.sub(" ", htmlmod.unescape(text)))
    return WS_RE.sub(" ", text).strip()


def duration(value):
    if not isinstance(value, str):
        return None
    m = DUR_RE.match(value.strip())
    if not m:
        return clean(value) or None
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    total = (d * 24 + h) * 60 + mi
    if total == 0:
        return None
    h, mi = divmod(total, 60)
    parts = []
    if h:
        parts.append(f"{h} hr")
    if mi:
        parts.append(f"{mi} min")
    return " ".join(parts)


def is_recipe(node):
    t = node.get("@type") if isinstance(node, dict) else None
    if isinstance(t, list):
        return any(isinstance(x, str) and x.lower() == "recipe" for x in t)
    return isinstance(t, str) and t.lower() == "recipe"


def find_recipe(obj):
    if isinstance(obj, list):
        for x in obj:
            r = find_recipe(x)
            if r:
                return r
    elif isinstance(obj, dict):
        if is_recipe(obj):
            return obj
        for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement", "item"):
            if key in obj:
                r = find_recipe(obj[key])
                if r:
                    return r
    return None


def image_url(img, base):
    if isinstance(img, list):
        for x in img:
            u = image_url(x, base)
            if u:
                return u
        return None
    if isinstance(img, dict):
        img = img.get("url") or img.get("contentUrl")
    if isinstance(img, str) and img.strip():
        return urljoin(base, img.strip())
    return None


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def instructions(raw):
    """Return [{"name": str|None, "steps": [str]}]."""
    sections, loose = [], []

    def step_text(s):
        if isinstance(s, str):
            return clean(s)
        if isinstance(s, dict):
            return clean(s.get("text") or s.get("name") or s.get("description"))
        return ""

    def walk(items):
        for it in as_list(items):
            if isinstance(it, dict) and str(it.get("@type", "")).lower() == "howtosection":
                steps = []
                for s in as_list(it.get("itemListElement")):
                    if isinstance(s, dict) and str(s.get("@type", "")).lower() == "howtosection":
                        walk([s])
                        continue
                    t = step_text(s)
                    if t:
                        steps.append(t)
                if steps:
                    sections.append({"name": clean(it.get("name")) or None, "steps": steps})
            elif isinstance(it, str) and ("\n" in it.strip() or "<" in it):
                for line in re.split(r"<br\s*/?>|</p>|</li>|\n", it):
                    t = clean(line)
                    if t:
                        loose.append(t)
            else:
                t = step_text(it)
                if t:
                    loose.append(t)

    walk(raw)
    if loose:
        sections.insert(0, {"name": None, "steps": loose})
    return sections


def normalise(node, base):
    ingredients = [clean(x) for x in as_list(node.get("recipeIngredient") or node.get("ingredients"))]
    ingredients = [x for x in ingredients if x]
    steps = instructions(node.get("recipeInstructions"))
    if not ingredients and not steps:
        return None
    y = node.get("recipeYield")
    if isinstance(y, list):
        y = next((x for x in y if isinstance(x, str) and not x.isdigit()), y[0] if y else None)
    return {
        "name": clean(node.get("name") or node.get("headline")) or "Recipe",
        "description": clean(node.get("description"))[:400] or None,
        "image": image_url(node.get("image"), base),
        "yield": clean(y) or None,
        "prep": duration(node.get("prepTime")),
        "cook": duration(node.get("cookTime")),
        "total": duration(node.get("totalTime")),
        "ingredients": ingredients,
        "sections": steps,
        "site": urlparse(base).netloc.removeprefix("www."),
    }


class _LdCollector(HTMLParser):
    """Collects JSON-LD blocks and simple schema.org microdata."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self._in_ld, self._buf = [], False, []
        self.micro = {"recipeIngredient": [], "recipeInstructions": [], "name": None, "image": None}
        self._in_recipe = 0
        self._depth = 0
        self._prop = None
        self._prop_depth = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower().startswith("application/ld+json"):
            self._in_ld, self._buf = True, []
            return
        void = tag in VOID
        if not void:
            self._depth += 1
        if not void and "recipe" in (a.get("itemtype") or "").lower() and not self._in_recipe:
            self._in_recipe = self._depth
        prop = a.get("itemprop")
        if self._in_recipe and prop:
            if prop == "image" and not self.micro["image"]:
                self.micro["image"] = a.get("src") or a.get("content") or a.get("href")
            elif prop in ("recipeIngredient", "ingredients", "recipeInstructions", "name") and self._prop is None:
                if a.get("content"):
                    self._store(prop, a["content"])
                elif not void:
                    self._prop, self._prop_depth, self._text = prop, self._depth, []

    def handle_startendtag(self, tag, attrs):
        if tag in VOID:
            self.handle_starttag(tag, attrs)
        # self-closing non-void tags (<div/>) open and close nothing for our purposes

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.blocks.append("".join(self._buf))
            self._in_ld = False
            return
        if tag in VOID:
            return
        if self._prop is not None and self._depth == self._prop_depth:
            self._store(self._prop, " ".join(self._text))
            self._prop = None
        if self._in_recipe and self._depth == self._in_recipe:
            self._in_recipe = 0
        self._depth = max(0, self._depth - 1)

    def _store(self, prop, text):
        text = clean(text)
        if not text:
            return
        if prop == "name":
            self.micro["name"] = self.micro["name"] or text
        elif prop == "ingredients":
            self.micro["recipeIngredient"].append(text)
        else:
            self.micro[prop].append(text)

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)
        elif self._prop is not None:
            self._text.append(data)


def extract_recipe(html, base_url):
    p = _LdCollector()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    for block in p.blocks:
        block = block.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            try:  # some sites put raw control characters / newlines inside strings
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", block))
            except json.JSONDecodeError:
                continue
        node = find_recipe(data)
        if node:
            r = normalise(node, base_url)
            if r:
                return r
    m = p.micro
    if m["recipeIngredient"] or m["recipeInstructions"]:
        return normalise(m, base_url)
    return None
