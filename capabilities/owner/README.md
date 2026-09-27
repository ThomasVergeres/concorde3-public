# Optional owner interface

The owner interface is a separate web inbox for questions, decisions, and private file handoffs. Instances use it through an MCP adapter and a supervised observer. It is not part of Concorde's scheduler. Each instance must be registered explicitly.

## Set up the service

Use Python 3.12+, an HTTPS hostname, and a verified email sender. Install the Python dependencies in a separate environment:

```sh
python3 -m venv ~/.local/share/concorde-owner-venv
~/.local/share/concorde-owner-venv/bin/pip install -r capabilities/owner/requirements.txt
~/.local/share/concorde-owner-venv/bin/python -m capabilities.owner.cli \
  --data ~/.local/share/concorde-owner/state setup \
  --origin https://YOUR_HOST --email YOU@example.com \
  --dotenv /private/path/.env --output ~/.local/share/concorde-owner/config.json
```

The service listens on loopback. Put an HTTPS reverse proxy in front of it; `deploy/Caddyfile` is an example. Keep the config, email credentials, and service state outside Git. The service sends a sign-in link, then lets the owner set up a passkey. Email remains the recovery path.

## Attach an instance

Pause the instance and let active work finish. Use paths reachable from the instance's actual execution environment.

```sh
concorde3 pause INSTANCE
python -m capabilities.owner.cli --data SERVICE_STATE register \
  --name INSTANCE_NAME --instance-id C3_ID --origin https://YOUR_HOST \
  --output INSTANCE/.concorde2/owner/connection.json
python -m capabilities.owner.attach --instance INSTANCE \
  --connection INSTANCE/.concorde2/owner/connection.json \
  --binary /absolute/path/concorde3 --intention purpose
concorde3 resume INSTANCE
```

Attachment adds a capability item and an observer program; it does not resume the instance on its own. If a step fails, leave the instance paused and retry that step.

The agent uses `owner_request` to ask for a specific decision. A reply supplies information or authority for that request; the agent still has to check the result with `owner_request_verify`. Secret handoffs use `owner_secret_read` or `owner_secret_materialize`. Keep secret values out of graph items, logs, and public artifacts.

## Operate and recover

`python -m capabilities.owner.cli --data SERVICE_STATE status` shows counts and service state. Check failed mail separately; a healthy web endpoint does not prove that an email arrived. To cut off an instance, use `disable --instance-id ID`. To replace its bearer token, use `rotate --instance-id ID --origin HTTPS_ORIGIN --output NEW_PRIVATE_FILE` and install the new file in that instance.

Back up the SQLite database consistently with its vault key. Losing the key makes stored secrets unreadable. Host administrators can read service data, so run the service under a dedicated account and protect its files.
