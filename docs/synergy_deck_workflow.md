# Building a commander-specific Synergy Deck

## Stages

1. **Freeze the training inputs.** Save the Scryfall Oracle snapshot and the collected deck previews. Capture the number of IDs in the public index, the number of previews actually fetched, unavailable lists, and duplicates. Resolve names to Oracle identities so alternate printings share a row. Keep the dataset hash and the date of the Card Kingdom price feed.
2. **Train the ranker and run a control.** Use non-flavor Oracle text, card types and subtypes, mana costs and color identity, power, and toughness. Train a masked-card ranker on the collected corpus. To measure the effect of a commander's own decklists, rerun with only those lists removed. Hold the catalog, model architecture, seed, optimizer, and evaluation queries fixed. Interpret top-ten recall as recovery of historical inclusions.
3. **Write the construction rules from the commander's actual actions.** Score when effects can happen, what they need, and what they enable. Give an engine both its enablers and its payoffs. Keep enough early ramp, interaction, and worthwhile plays at the relevant mana values. Generate from the legal pool with multiple search starts and try both individual and coordinated replacements.
4. **Build the mana base around the finished spell core.** Price each card using the cheapest in-stock legal Card Kingdom printing or combination of printings. Include the commander in the nonland subtotal. Aim for a lands subtotal near the nonland subtotal and use that as the starting allowance. Spend where a land improves early fixing, conditional entry, fetch targets, or an interaction the commander can use. Try several land and ramp counts and compare land-only variants with the spells fixed. Treat price parity as a target so the search can leave headroom when further spending adds little.
5. **Compare results on concrete lines of play.** Use the same opening-hand simulation for both lists and test the turns the deck cares about. Record which important cards changed and what they do. For each finalist, check the exact import on EDHPowerLevel. Use the **72.5%–77.5% playability** window to select a candidate and record EDHPowerLevel's bracket estimate. Repeat the exact-list check after any card substitution.
6. **Publish one verified list on Synergy Deck.** Save its report, shopping choices, export, and card-list hash. Confirm the site downloads the same 100 cards in the same commander configuration and shows the result attached to that hash.

## Doctor and Rose worked example

- **Collection and model.** The source index returned 5,623 previews. After removing three exact duplicates, the ranker trained on 5,620 distinct Doctor + Rose lists in a 180,930-list training set. The matched control excluded those 5,620 lists. Held-out top-ten recall was 46.3% with them and 44.8% without them. Both constructed lists kept Jhoira of the Ghitu, Clockspinning, and Passionate Archaeologist.
- **Rules and list search.** The initial optimizer gave Myojin of Cleansing Fire sweeper credit even when the Doctor cast it from exile. The revised rules credit only a hand-cast Myojin for that effect. They also treat Zethi's kicked recursion as setup-dependent and grade Tidewalker's contribution to Rose as modest. The new search scores repeatable suspend sources together with countdown effects and exile payoffs. All three weak inclusions dropped out while the neural weights stayed fixed.
- **Mana trial and release.** The first revised list used $55.65 in lands. A land-only pass kept the 38 land slots and tested spending about $25 more on faster color access. Steam Vents, Sacred Foundry, and three fast lands replaced slower fixing. The published list priced at $79.62 in lands and $81.82 in nonlands, just over 97% parity. The same land-aware simulation put turn-five Doctor attacks at 68.8%, up from 61.9%. The exact list checked at 76.0% playability and estimated Bracket 3. Its list hash starts with `fdcd310a3d6e`. [Published Doctor and Rose deck](https://cinacho23.github.io/edhrec-affinity/#/synergy-deck?commander=the-tenth-doctor-rose-tyler)

## Pantlaza adaptation

- Score printed Dinosaurs, Dinosaur tokens, high toughness, cheap proactive discover hits, and legal ways to get Dinosaurs onto the battlefield on other players' turns. Pantlaza's own four toughness gives the deck a discover 4 on entry. Each instance can discover once each turn. Blinking Pantlaza makes a new instance with its own trigger. The toughness of a different Dinosaur only matters while that Pantlaza can still discover. [The Lost Caverns of Ixalan release notes](https://magic.wizards.com/en/news/feature/the-lost-caverns-of-ixalan-release-notes)
- Treat a hand-cast-only sweeper as conditional. Discount overlapping ways to create the same entry, and prefer outlets that work with the actual spell core. Prioritize green sources for early ramp. Credit Cavern of Souls and similar lands for casting Dinosaurs, with a separate count for unrestricted sources that can cast ramp and interaction.
- Train both arms from the same frozen target file. The files preserve the difference between the full indexed population and the previews collected for training. Vary the lands and early ramp together, then try land allocations from 90% to 105% of each candidate's nonland subtotal. Select a checked list with a coherent Dinosaur and discover plan and a land subtotal close to the spells.

## Reproduce the stages

Use the project's Python environment with the prototype and validation extras and Playwright Chromium installed. Keep the raw source snapshots and model files outside the frontend. Card Kingdom price snapshots are generated with `edhrec_affinity.synergy_prototype.prices` from the public feed plus Scryfall's eligible paper printings.

For Doctor and Rose, use `python -m edhrec_affinity.synergy_prototype generate --target-previews … --save-ranker …` with the Oracle snapshot, aliases, and background corpus. Then use `python -m edhrec_affinity.synergy_prototype refine --trained-ranker … --oracle-jsonl … --prices … --output-directory … --check`. Recheck any land-only edits with the same module's `check` command. The audited production list is in `frontend/src/generated/synergy-deck.json`.

For Pantlaza, collect previews with the project's existing WebKit collector and shared request pacing. Its collection state contains both the public index and the observed previews. The `train` command merges observations by deck ID before filtering and deduplicating training examples.

```bash
python -m edhrec_affinity.synergy_prototype.pantlaza_pipeline train \
  --oracle-jsonl "$oracle_snapshot" --catalog-updated-at "$oracle_date" \
  --alias-cache data/raw/distinctive-core-card-metadata.json \
  --previews data/processed/build-styles-current/_collection/previews \
  --current-previews "$pantlaza_checkpoint" \
  --historical-previews data/processed/build-styles-current/_collection/previews/pantlaza-sun-favored.json.gz \
  --output-directory "$model_directory"

python -m edhrec_affinity.synergy_prototype.pantlaza_pipeline generate \
  --oracle-jsonl "$oracle_snapshot" --prices "$retail_snapshot" \
  --model-directory "$model_directory" --output-directory "$candidate_directory" --check
```

Both arms export exact checked finalists and their priced printings. Compare the finalists using the same playability and game-plan criteria. The Pantlaza page reads the published report from `frontend/src/generated/synergy-deck-pantlaza.json`. Use the frontend-only deploy workflow pinned to the live site data version, then verify the exported list on the deployed page.

## Pantlaza run record

The November 26 Toronto-time index contained 38,404 public entries. This trial fetched 673 fresh previews and added 295 historical previews with distinct IDs. Eight of the 968 observations did not pass the training qualifications, leaving **960 distinct Pantlaza lists** in the 172,742-list training set. The same typed network had 51,841 parameters in each arm. Held-out top-ten inclusion recall was 46.42% with those lists and 47.67% in the 171,782-list control.

The first spell pass gave sweeper credit to Glyph of Reincarnation, which needs a Wall that blocked, and added snow ramp without suitable lands. The corrected pass requires early land ramp that can actually find targets and checks the timing of activated entries. The mana solver then improved the weakest colors jointly and discounted lands that are lost if a creature or artifact is absent.

| Checked finalist | Lands | Land subtotal | Nonland subtotal | EDHPowerLevel playability | Simulated Pantlaza by turn four |
| --- | ---: | ---: | ---: | ---: | ---: |
| Model with Pantlaza examples, first 39-land finalist | 39 | $133.12 | $134.53 | 74.6% | 62.0% |
| Model with Pantlaza examples, land-only revision | 39 | $140.07 | $134.53 | 75.1% | 63.0% |
| Model with Pantlaza examples, larger mana base | 41 | $131.03 | $131.62 | 77.5% | 63.9% |
| Model without Pantlaza examples | 41 | $122.43 | $118.54 | 73.3% | 58.1% |

The fixed-spell 39-land comparison also tested Cavern of Souls with `build_mana_base` and its `required` argument at 95%, 100%, and 105% of the nonland subtotal. At this price the Cavern variants gave up unrestricted fixing. The published 39-land list had room for three shocklands, utility fixing, and Unclaimed Territory. Its exact list stayed near the center of the playability window and retained two more spell slots than the 41-land variant.

The final deck contains **29 printed Dinosaurs**, including Pantlaza, eight cards classified as early land ramp, and five blink effects. The land-only pass kept its spell list intact. A simplified simulation used 12,000 openings per finalist and modeled land entry conditions, casting colors and early ramp. The bracket estimate for the published list is 2. Its exact hash starts with `25195223ee30`. [Published Pantlaza deck](https://cinacho23.github.io/edhrec-affinity/#/synergy-deck?commander=pantlaza-sun-favored)

Run `python -m edhrec_affinity.synergy_prototype.pantlaza_benchmark --oracle-jsonl "$oracle_snapshot" --catalog-updated-at "$oracle_date" REPORTS...` to compare openings on the same simulator.
