---
title: GigBid
emoji: 📝
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Multi-agent freelancer copilot (AI-4016), on simulated data
---

# GigBid

A multi-agent freelancer copilot: it finds jobs, judges whether they are worth a bid, drafts
proposals grounded in portfolio evidence, and waits for a human before anything is sent.

This Space runs the whole stack in one container on DevSim, a simulated marketplace: no real
client is ever contacted. Sign in with the reviewer login given in the submission. Open the app
at its direct address (the `.hf.space` link) rather than inside the Hugging Face page, so the
sign-in cookie works.

The free tier has no persistent disk: each restart seeds a fresh demo, and work from an earlier
session is not kept.

Source, results and documentation: https://github.com/FatimaSohailll/GigBid
