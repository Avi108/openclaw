# Stock analyst agent (workspace template)

## Setup

1. Export the "Avinash USA Stocks" Claude project discussions to text/markdown and put them in `research/raw/`. (Claude Projects have no API this agent can read; export manually.)
2. Create the agent:

   ```bash
   openclaw agents add stocks --workspace ./examples/stock-analyst-agent
   ```

3. Ask it: "Read everything in research/raw and build the notes in research/notes per AGENTS.md."
4. Review the notes and fix `RISK-POLICY.md`.
5. Broker: INDmoney's public API availability is unconfirmed. Once the user has official API access, add it as an MCP/plugin tool and enable Phase 3. Until then the agent only proposes trades.
