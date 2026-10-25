# Deploying PitchIQ to Azure Container Apps

Not deployed yet. Creating these resources costs money, so it waits for the project owner's approval. The commands
below are the intended path (spec 19) and have not been run.

The shape: one container with the compiled frontend and the FastAPI/WebSocket API. One replica and one worker,
because replay sessions live in memory. Public ingress on port 8000. The image holds the public clips only.

```bash
# variables (fill in; nothing here is a real value)
RG=<resource-group>; LOC=<region>; ACR=<registry-name>; ENV=<containerapps-env>; APP=pitchiq

docker build -f infra/Dockerfile -t $ACR.azurecr.io/pitchiq:0.2.0 .
docker run --rm -p 8000:8000 $ACR.azurecr.io/pitchiq:0.2.0   # local check: http://127.0.0.1:8000, /api/health

az acr create -g $RG -n $ACR --sku Basic
az acr login -n $ACR && docker push $ACR.azurecr.io/pitchiq:0.2.0
az containerapp env create -g $RG -n $ENV -l $LOC
az containerapp create -g $RG -n $APP --environment $ENV \
  --image $ACR.azurecr.io/pitchiq:0.2.0 --registry-server $ACR.azurecr.io \
  --ingress external --target-port 8000 --min-replicas 1 --max-replicas 1 \
  --system-assigned \
  --env-vars LLM_MODE=offline AUTH_MODE=managed_identity
```

To turn on Foundry narration:
1. Give the app's system-assigned identity a role on the Foundry project that allows model calls.
2. Set `LLM_MODE=foundry`, `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL` on the container app.
3. Set `PITCHIQ_PUBLIC_HOSTS` to the app's ingress host name
   (`az containerapp show -g $RG -n $APP --query properties.configuration.ingress.fqdn -o tsv`), so `/mcp` accepts
   the public Host header (other hosts get 421).
4. Check `/api/health`. `foundry_configured` only means the settings are present. Then open one replay and confirm that
   `last_cloud_result.last_call_succeeded` becomes true after a real call. An identity that exists is not
   necessarily allowed to call the project.

Check after deploying, in a clean browser:
- the pitch replays
- the WebSocket updates
- seek starts a new epoch
- approve shows the approved revision on the broadcast view
- export returns overlay JSON
