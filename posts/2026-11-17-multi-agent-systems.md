---
date: 2026-11-17
type: skill
tone: humorous
format: dry_take
title: "Multi-agent systems"
linkedin_url: https://www.linkedin.com/feed/update/urn:li:share:7528428997962133504/
---

AWS shipped AgentCore updates this week. Sessions start 15x faster, scale to 8 vCPUs, and each runs in its own Firecracker micro-VM.

The A2A protocol is now a Linux Foundation thing, which means marketing finally has a whitepaper to point at while engineers figure out why two agents won't handshake.

I keep thinking about the microservices hangover. The industry spent years splitting monoliths into services nobody could trace, then built entire companies selling tools to stare at distributed traces. With agents, we get to do it again, except now the services also hallucinate.

The AWS reference architecture shows an orchestrator pulling skills from Agent Cards without touching existing code. Works on paper. Reality is someone's JSON specification being subtly wrong and nobody knowing why suppliers stopped getting paid until 3am.

Small agents with narrow jobs mean logs I can actually read. Failures that stay in their lane. When one decides "refund" means "refund everything including inventory that never shipped," I stop that one. The rest keep running.

Mission reported a 17% drop in runtime costs with the new AgentCore. That's nice. What I'd actually want in production is a kill switch I trust, an orchestrator that knows what "done" looks like, and enough isolation that I don't have to explain to finance why one creative agent paused every supplier payment for twenty minutes.

The frameworks make it possible. Knowing which plug to pull, and when, is still your job.

#MultiAgentSystems #EnterpriseReliability #AgenticAI

<!-- sources
Run agents securely and efficiently with the next generation AgentCore runtime: https://aws.amazon.com/blogs/machine-learning/run-agents-securely-and-efficiently-with-the-next-generation-agentcore-runtime/
Build composable, interoperable multi-agent systems with A2A on AWS: https://aws.amazon.com/blogs/machine-learning/build-composable-interoperable-multi-agent-systems-with-a2a-on-aws/
-->
