# Review and launch an RL run

RL recipes describe supported evaluation and training runs. Recipe availability
reflects operator qualification and current launch settings. Admission and
capacity are checked again at launch, so an available recipe can still fail to
launch.

Start by finding the recipe you intend to use and checking its current status:

```python
import nodus

def find_recipe(client):
    recipes = client.rl.list_recipes()
    recipe = next(
        (
            item for item in recipes
            if item.id == "reasoning-gym-leg-counting"
        ),
        None,
    )

    if recipe is None:
        raise RuntimeError("The requested RL recipe is not offered")
    if not recipe.available:
        reason = recipe.unavailable_reason or "No reason was provided"
        raise RuntimeError(f"RL recipe is unavailable: {reason}")
    return recipe
```

Build the configuration explicitly, then ask the server to normalize and
review it. Preview does not launch a workload.

```python
def prepare_run(client, recipe):
    configuration = {
        "recipe_id": recipe.id,
        "recipe_version": recipe.version,
        "mode": "evaluate",
        "evaluation_tasks": 16,
        "training_steps": 20,
        "max_cost_usd": 5,
        "seed": 42,
        "include_traces": False,
    }

    preview = client.rl.preview(configuration)
    print(preview.estimate)
    print(preview.phases)
    print(preview.outputs)

    if not preview.launchable:
        raise RuntimeError(
            "RL run cannot launch: " + ", ".join(preview.blocking_reasons)
        )
    return preview
```

Inspect the normalized configuration, phases, outputs, and estimate before
launching. The server returns a review token for that exact preview. A changed
configuration needs a new preview and review token.

Launch only after your application or a person has accepted the preview. Use a
stable idempotency key for one intended run and store it with your own run
record.

```python
def launch_run(client, preview, stable_run_id):
    workload = client.rl.launch(
        configuration=preview.configuration,
        review_token=preview.review_token,
        idempotency_key=stable_run_id,
    )
    print(workload.id)
    return workload
```

`launch()` requires all three values. It does not select a recipe, fill in a
configuration, or generate an idempotency key. This makes the paid action
explicit and lets an application retry safely.

If a timeout or connection failure leaves the launch outcome unclear, retry
the same configuration and review token with the same idempotency key. A new
key can create another paid run. Reusing one key with a different request
raises `IdempotencyConflictError`.

Normal SDK exceptions apply:

```python
def launch_with_error_handling(client, preview, stable_run_id):
    try:
        return client.rl.launch(
            configuration=preview.configuration,
            review_token=preview.review_token,
            idempotency_key=stable_run_id,
        )
    except nodus.IdempotencyConflictError:
        raise RuntimeError("The idempotency key belongs to a different request")
    except nodus.NodusError as error:
        raise RuntimeError(f"RL launch failed: {error}") from error
```

The asynchronous client has the same flow:

```python
async def prepare_async(client, configuration):
    recipes = await client.rl.list_recipes()
    recipe = next(
        (item for item in recipes if item.id == configuration["recipe_id"]),
        None,
    )
    if recipe is None or not recipe.available:
        raise RuntimeError("The requested RL recipe is unavailable")
    preview = await client.rl.preview(configuration)
    if not preview.launchable:
        raise RuntimeError(
            "RL run cannot launch: " + ", ".join(preview.blocking_reasons)
        )
    return preview

async def launch_async(client, preview, stable_run_id):
    return await client.rl.launch(
        configuration=preview.configuration,
        review_token=preview.review_token,
        idempotency_key=stable_run_id,
    )
```
