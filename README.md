# Shopping List Catalog

Product catalogs for [Shopping List Card](https://github.com/eyalgal/ha-shopping-list-card), managed in Home Assistant. Each catalog is a JSON file with its own sensor, so you can keep one per store and edit products from the card instead of by hand.

- One sensor per catalog, such as `sensor.shopping_catalog_trader_joes` and `sensor.shopping_catalog_costco`.
- No `command_line` sensor or `json_attributes` list to keep in sync. New categories appear immediately.
- Edit categories, products, and variants from the card's editor (Shopping List Card 3.1.0 or later).
- Learns from the items you check off and suggests what to buy next (Shopping List Card 3.2.0 or later shows them).
- Saves are atomic, keep the previous version as `<file>.bak`, and never overwrite changes made elsewhere since you opened the editor.

## Install

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eyalgal&repository=ha-shopping-list-catalog&category=integration)

1. Use the button above, or in HACS open the three-dot menu > **Custom repositories**, add `https://github.com/eyalgal/ha-shopping-list-catalog` as an **Integration**, then download **Shopping List Catalog**.
2. Restart Home Assistant.

## Add a Catalog

[![Open your Home Assistant instance and start setting up Shopping List Catalog.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=shopping_list_catalog)

Go to **Settings > Devices & Services > Add Integration > Shopping List Catalog**, name the catalog, and choose where its products come from:

| Start from | Result |
|---|---|
| Starter products | A small example catalog in `shopping_catalogs/<name>.json` |
| Empty catalog | An empty catalog in `shopping_catalogs/<name>.json` |
| Existing JSON file | Uses a file you already have, such as `shopping_items.json`, and edits it in place |
| Copy of another catalog | Starts with the products of a catalog you already added |

Add the integration again for each store. Paths must be `.json` files inside the configuration directory.

## Use It in the Card

```yaml
type: custom:shopping-list-card
mode: catalog
catalog_entity: sensor.shopping_catalog_trader_joes
catalog_attribute: catalog
todo_list: todo.shopping_list
```

The card's visual editor fills in `catalog_attribute` when you pick the sensor. With Shopping List Card 3.1.0 or later, administrators see **Edit products** in the card editor.

## Purchase History

Each catalog can learn what you buy. Open the catalog under **Settings > Devices & Services > Shopping List Catalog**, select **Configure**, and choose the to-do lists it should learn from, usually the list its cards add to.

- An item checked off on one of those lists is logged as a purchase with its name, quantity, and date, whichever app checked it off. Removing an item without checking it off is not a purchase, and unchecking an item within a day undoes it.
- The history is kept in Home Assistant's storage, separate from the recorder, so it is not purged. Check-offs made while Home Assistant was offline are picked up when it starts.
- When a list from the built-in **Shopping List** integration is chosen for the first time, its check-offs still in the recorder (usually the last 10 days, or as configured with `purge_keep_days`) are imported once.
- The catalog sensor's `purchases_logged` attribute shows how many purchases are logged.

### Suggestions

<img src="https://raw.githubusercontent.com/eyalgal/ha-shopping-list-card/main/docs/images/catalog-suggested.png" alt="Catalog with the Suggested filter on, showing nine usually bought products across Fruits, Vegetables, and Condiments, with Snap Peas already on the list and a tooltip on Pear: usually bought every 10 days, last bought 9 days ago" width="812"/>

From the history, each catalog suggests what you probably need now. An item is suggested when it was bought on at least two different days, not in the last 4 days, and either most of its usual interval has passed or it was bought in at least 2 of the last 3 weeks.

- The catalog sensor's `suggestions` attribute maps each suggested item to `last` (date), `since` (days ago), `every` (usual days between purchases), and `quantity` (usual amount), most overdue first. The Shopping List Card's **Suggested** filter (3.2.0 or later) reads it.
- A second sensor, for example `sensor.shopping_catalog_supermarket_suggested`, counts the suggestions and lists them in its `items` attribute, which is handy for notifications.
- Suggestions update whenever a purchase is logged, when the catalog changes, and hourly.
- Purchases are matched to catalog products ignoring case and simple plurals, and amounts typed after the name count as quantities, so `Pears`, `Pear (2)`, and `Pear 2` are all the catalog's Pear.

### Products Missing From the Catalog

An item you check off on at least two different days that isn't in the catalog is offered in the Shopping List Card's **Edit products** (3.3.0 or later), under **Bought but not in the catalog**. When the name looks like a variant of an existing product, such as `Mini cucumbers` for Cucumber or `Chicken wings` for Chicken, it can be added as that variant; otherwise you choose the category for a new product. Dismissed items aren't offered again.

## Moving From the command_line Sensor

1. Add this integration with **Existing JSON file** and your current path, usually `shopping_items.json`.
2. Point your catalog cards at the new sensor and set `catalog_attribute: catalog`.
3. Remove the old `command_line` sensor from `configuration.yaml` and reload Command Line entities.

## Good to Know

- The sensor's state is the number of products. The catalog is in its `catalog` attribute, which is excluded from history because large catalogs exceed the recorder's size limit.
- Edits made to the file by hand are picked up within 30 seconds. A file that is missing or invalid makes the sensor unavailable until it is fixed.
- Saving from the editor rewrites the file with 2-space indentation. Fields the editor does not know about are kept.
- Only administrators can save. Removing a catalog from Home Assistant keeps its file and its purchase history.

## Format

The same format Shopping List Card reads: category names mapped to arrays of products. See the card's [JSON catalog guide](https://github.com/eyalgal/ha-shopping-list-card/tree/main/examples/auto-generated-grid#product-json).

```json
{
  "Fruits": [
    { "title": "Apple", "types": ["Gala", "Granny Smith"] },
    { "title": "Banana" }
  ]
}
```

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```
