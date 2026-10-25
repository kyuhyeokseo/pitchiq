# Deploying PitchIQ to Azure Container Apps

The public demo was deployed with the commands below on 2026-10-24. Resource names other than `pitchiq` and
`pitchiq-env` are placeholders here.

The shape (spec 19): one container with the compiled frontend and the FastAPI/WebSocket API, one replica and one
worker, because replay sessions live in memory. Public ingress on port 8000. The image holds the public clips only.

The image comes from CI: every push to `main` runs the tests, builds `infra/Dockerfile`, smoke-tests it offline (health,
12 clips, 5 MCP tools, no private material in the image) and pushes `ghcr.io/kyuhyeokseo/pitchiq:sha-<commit>` and
`:latest`. The package is public, so the Container App needs no registry credentials.

```bash
SUB="<subscription>"; RG=<resource-group>; LOC=swedencentral
FOUNDRY_ID=/subscriptions/<sub-id>/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/<foundry-resource>
ENDPOINT=https://<foundry-resource>.services.ai.azure.com/api/projects/<project>

az extension add -n containerapp
az provider register -n Microsoft.App --subscription "$SUB" --wait

# the environment's console logs go to the Log Analytics workspace the Foundry resource already has
KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n <workspace> --subscription "$SUB" --query primarySharedKey -o tsv)
az containerapp env create -g $RG -n pitchiq-env -l $LOC --subscription "$SUB" \
  --logs-workspace-id <workspace-customer-id> --logs-workspace-key "$KEY"
FQDN=pitchiq.$(az containerapp env show -g $RG -n pitchiq-env --subscription "$SUB" --query properties.defaultDomain -o tsv)

az containerapp create -g $RG -n pitchiq --environment pitchiq-env --subscription "$SUB" \
  --image ghcr.io/kyuhyeokseo/pitchiq:sha-<commit> --ingress external --target-port 8000 --transport auto \
  --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi --system-assigned \
  --env-vars LLM_MODE=foundry AUTH_MODE=managed_identity FOUNDRY_PROJECT_ENDPOINT=$ENDPOINT FOUNDRY_MODEL=gpt-5-mini \
             FOUNDRY_DAILY_LIMIT=300 FOUNDRY_REASONING_EFFORT=minimal PITCHIQ_PUBLIC_HOSTS=$FQDN

# the app's identity may call the Foundry project, and nothing else
PID=$(az containerapp show -g $RG -n pitchiq --subscription "$SUB" --query identity.principalId -o tsv)
az role assignment create --assignee-object-id $PID --assignee-principal-type ServicePrincipal \
  --role "Foundry User" --scope $FOUNDRY_ID --subscription "$SUB"
```

A new release is a new image tag:

```bash
az containerapp update -g $RG -n pitchiq --subscription "$SUB" --image ghcr.io/kyuhyeokseo/pitchiq:sha-<commit>
```

Settings:
- `FOUNDRY_DAILY_LIMIT` caps the Foundry runs (narration pipelines, questions and recaps) per UTC day, so visitors cannot
  spend the subscription's credit. Beyond it the deterministic plans and answers are shown, labelled.
- `PITCHIQ_PUBLIC_HOSTS` is the ingress host name, so `/mcp` accepts the public Host header (other hosts get 421).
- One replica on purpose: sessions are in memory. A restart clears them and resets the daily counter.

Check after deploying:
1. `/api/health`: `foundry_configured` only means the settings are present. Open one replay and confirm that
   `last_cloud_result.last_call_succeeded` becomes true after a real call. An identity that exists is not necessarily
   allowed to call the project, and a new role assignment can take a few minutes.
2. `POST /mcp` with `{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}` lists 5 tools.
3. The browser walkthrough against the public URL, on a machine with Playwright:
   `PYTHONPATH=backend .venv/bin/python evaluation/walkthrough/walkthrough.py --out <dir> --base-url https://$FQDN`.
   Its "at most 2 replays open" check assumes nobody else is using the app at the time.
