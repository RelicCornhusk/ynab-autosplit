<div align="center">
  <h1>Partner Split for YNAB</h1> 
  <h3>A background worker that automatically syncs shared credit card expenses to an IOU account in YNAB.</h3>
  <img width="147" height="58.5" alt="image" src="https://api.ynab.com/papi/works_with_ynab.svg" />
</div>
<br />

**New to cost sharing in YNAB?** [cost-sharing-for-ynab](costsharingforynab.com) is an excellent introduction to the overall concept and how to set up your YNAB accounts correctly before using this tool. I recommend using it a few times (in Standard mode) before using this automation.

## What it does

If you share a credit card with a partner or family member, keeping your YNAB budget accurate is a pain. Every time a transaction comes in on the shared card, you have to manually figure out your portion and record an IOU somewhere.

`partner-split-for-ynab` solves this by running as a background job. It watches your shared credit card account in YNAB, finds any new approved and categorized transactions, and automatically creates a corresponding split transaction in a separate IOU account — at whatever percentage of the cost is yours. Processed transactions are flagged green so they're never double-counted.

## Inspiration

This project was inspired by [cost-sharing-for-ynab](https://github.com/chelseaSchmidt/cost-sharing-for-ynab) by Chelsea Schmidt, which works through a web UI where you select transactions within a time range and trigger a split transaction from the IOU account. `partner-split-for-ynab` is an alternative for people who'd rather have this happen automatically on a schedule, without any manual intervention.

This project uses the standard model from Cost Sharing for YNAB, where all shared expenses flow through a shared account and your portion is mirrored into a separate IOU tracking account. This is distinct from the category-based approach where you have shared expenses parent category.

## How it works

1. Fetches recent transactions from your shared account in the last 30 days (the number of days can be tweaked)
2. Filters transactions that are approved, categorized, and not yet flagged green
3. Creates a single split transaction in your IOU account — one subtransaction per original transaction, preserving the category and payee, scaled to your partner's share of the expense. This transaction will be marked as approved and uncleared.
4. Flags all processed transactions from the shared account green so they won't be picked up again on the next run

## Configuration

All configuration is done via environment variables.

| Variable | Required | Default | Description |
|---|---|---|---|
| `YNAB_ACCESS_TOKEN` | ✅ | — | Your YNAB personal access token. Generate one at [app.ynab.com/settings/developer](https://app.ynab.com/settings/developer). |
| `YNAB_PLAN_NAME` | ✅ | — | The exact name of your YNAB plan. |
| `YNAB_SHARED_ACCOUNT_NAME` | ✅ | — | The exact name of the shared credit card account in YNAB to watch for new transactions. |
| `YNAB_IOU_ACCOUNT_NAME` | ✅ | — | The exact name of the account where your share of the costs will be recorded as a split transaction. |
| `YNAB_IOU_PERCENTAGE` | ✅ | — | Your share of shared expenses as an integer (e.g. `50` for half). |
| `YNAB_LOOKBACK_DAYS` | ❌ | `30` | How many days back to look back for unprocessed transactions. |


## Docker

A Docker image is available. The script runs once and exits, so you'll want to wrap it in a scheduler (see below).

```bash
docker run --rm \
  -e YNAB_ACCESS_TOKEN=your_token_here \
  -e YNAB_PLAN_NAME="My Budget" \
  -e YNAB_SHARED_ACCOUNT_NAME="Shared Visa" \
  -e YNAB_IOU_ACCOUNT_NAME="Partner IOU" \
  -e YNAB_IOU_PERCENTAGE=50 \
  ghcr.io/reliccornhusk/partner-split-for-ynab:latest
```

## Deployment

### Docker Compose with a cron sidecar

A simple self-hosted setup using [mcuadros/ofelia](https://github.com/mcuadros/ofelia) as a lightweight cron scheduler. All environment variables are loaded from a `.env` file so you don't have to commit sensitive values like your account names to your repo.

```yaml
services:
  partner-split-for-ynab:
    image: ghcr.io/reliccornhusk/partner-split-for-ynab:latest
    env_file: .env

  scheduler:
    image: mcuadros/ofelia:latest
    depends_on:
      - partner-split-for-ynab
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    command: daemon --docker
    labels:
      ofelia.job-run.partner-split-for-ynab.schedule: "@every 5m"
      ofelia.job-run.partner-split-for-ynab.container: "partner-split-for-ynab"
```

Create a `.env` file alongside your `docker-compose.yml` (and add it to `.gitignore`):

```env
YNAB_ACCESS_TOKEN=your_token_here
YNAB_PLAN_NAME=My Plan
YNAB_SHARED_ACCOUNT_NAME=Shared Visa
YNAB_IOU_ACCOUNT_NAME=Partner IOU
YNAB_IOU_PERCENTAGE=50
YNAB_LOOKBACK_DAYS=30
```

### Kubernetes CronJob

All environment variables are pulled from a single Kubernetes Secret, so nothing sensitive ends up in your GitOps manifests.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: partner-split-for-ynab
  namespace: finance
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: partner-split-for-ynab
              image: ghcr.io/reliccornhusk/partner-split-for-ynab:latest
              envFrom:
                - secretRef:
                    name: partner-split-for-ynab-config
```

Create the secret with all your config in one shot:

```bash
kubectl create secret generic partner-split-for-ynab-config \
  --from-literal=YNAB_ACCESS_TOKEN=your_token_here \
  --from-literal=YNAB_PLAN_NAME="My Plan" \
  --from-literal=YNAB_SHARED_ACCOUNT_NAME="Shared Visa" \
  --from-literal=YNAB_IOU_ACCOUNT_NAME="Partner IOU" \
  --from-literal=YNAB_IOU_PERCENTAGE=50 \
  --from-literal=YNAB_LOOKBACK_DAYS=30 \
  -n finance
```

### Serverless options

If you'd rather run this on the cloud, this script is a good fit for a scheduled cloud function — AWS Lambda with EventBridge Scheduler, Google Cloud Functions with Cloud Scheduler, or Azure Functions with a timer trigger all work well. Package the code as a container or zip, point the trigger at your desired interval, and inject the environment variables through your cloud provider's secrets manager. The script has no persistent state and exits cleanly after each run, which maps naturally to the function execution model.

## Running locally

The following snippet can be used for local testing.
Requires Python 3.14+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/RelicCornhusk/partner-split-for-ynab.git
cd partner-split-for-ynab

export YNAB_ACCESS_TOKEN=your_token_here
export YNAB_PLAN_NAME="My Budget"
export YNAB_SHARED_ACCOUNT_NAME="Shared Visa"
export YNAB_IOU_ACCOUNT_NAME="Partner IOU"
export YNAB_IOU_PERCENTAGE=50

uv run main.py
```

## A note on rate limits

The YNAB API allows 200 requests per hour. Each run of this script makes roughly 4–5 API calls, so running every 5 minutes (~60 calls/hour) keeps you well within limits. You could safely run it every minute if you wanted near-real-time syncing.

## Legal disclaimer

We are not affiliated, associated, or in any way officially connected with YNAB or any of its subsidiaries or affiliates. The official YNAB website can be found at https://www.ynab.com.
The names YNAB and You Need A Budget, as well as related names, tradenames, marks, trademarks, emblems, and images are registered trademarks of YNAB. 