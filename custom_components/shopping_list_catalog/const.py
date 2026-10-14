"""Constants for Shopping List Catalog."""

from datetime import timedelta

DOMAIN = "shopping_list_catalog"

CONF_PATH = "path"
CONF_SOURCE = "source"
CONF_TODO_LISTS = "todo_lists"

SOURCE_STARTER = "starter"
SOURCE_EMPTY = "empty"
SOURCE_EXISTING = "existing"
SOURCE_COPY_PREFIX = "copy:"

CATALOG_DIR = "shopping_catalogs"
DEFAULT_EXISTING_PATH = "shopping_items.json"
ATTR_CATALOG = "catalog"
ATTR_PURCHASES = "purchases_logged"

SCAN_INTERVAL = timedelta(seconds=30)
MAX_CATALOG_BYTES = 1_000_000

STARTER_CATALOG = {
    "Fruits": [
        {"title": "Apple", "types": ["Gala", "Granny Smith"]},
        {"title": "Banana"},
    ],
    "Dairy and Eggs": [
        {"title": "Milk"},
        {"title": "Milk", "subtitle": "Lactose-free"},
        {"title": "Eggs"},
    ],
}
