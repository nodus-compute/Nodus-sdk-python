# Run your own RL code or a prepared recipe

You can start with your own training command and optional data. You do not need
to select a catalog environment. To show reported RL task progress, add the
optional `rl` metadata to a normal run:

```python
def launch_custom_rl(
    client, *, image, source_asset_id, budget, stable_run_id,
    model_label, planned_tasks,
):
    if not isinstance(stable_run_id, str) or not stable_run_id:
        raise ValueError("stable_run_id must be a nonempty stable key for this run")
    if budget is None:
        raise ValueError("budget must be an explicit spending limit for this run")
    return client.run(
        image=image,
        source_asset_id=source_asset_id,
        command=["python", "train.py"],
        outputs={"results": "outputs"},
        budget=budget,
        compute_class="accelerator",
        idempotency_key=stable_run_id,
        extra={
            "rl": {
                "schema_version": 1,
                "environment_id": "custom",
                "mode": "train",
                "model": model_label,
                "planned_tasks": planned_tasks,
            }
        },
    )
```

Use an imported source asset containing your `train.py`, a compatible runtime
image and an explicit spending limit. The command must write final results
under `outputs` and write and load its own checkpoint state. Adjust the command
and output path to match your project.

The metadata describes your experiment. Your code implements the trainer,
model, task limit and task-event reporting. Use `mode="evaluate"` for evaluation
without training. Custom runs cannot set `rl.recipe` or claim managed recipe
validation. A completed command without task events has no reported RL score.

## Use a prepared recipe

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
