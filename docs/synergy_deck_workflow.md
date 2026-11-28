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

## Y’shtola adaptation

Y’shtola needs a noncreature spell with mana value 3 or more on the stack. The optimizer scores the mode actually cast and gives credit to cost reductions and effects that reopen mana for a second cast. Lower-mana interaction and ramp still have jobs in the deck. The end-step draw needs one player to have lost at least four life by the time the step begins. Score life payments, extra damage to that same player, and abilities that turn Y’shtola’s life gain into more life lost. Treat effects that start at the end step, such as Wound Reflection, as finishers. [FINAL FANTASY release notes](https://magic.wizards.com/en/news/feature/final-fantasy-release-notes)

Use the same commands as the Pantlaza run with `python -m edhrec_affinity.synergy_prototype.commander_pipeline --commander yshtola-nights-blessed` in place of the Pantlaza module. Pass the Y’shtola checkpoints to `train`, then check both arms with `generate --check`. Use `python -m edhrec_affinity.synergy_prototype.opening_benchmark` with the same Oracle arguments to compare early access to all three colors and four mana. The published list lives in `frontend/src/generated/synergy-deck-yshtola.json`.

### Y’shtola run record

The November 27 deck gallery indexed 60,703 preview IDs. This run fetched 670 fresh lists and merged 293 additional historical lists, for 963 distinct Y’shtola lists in the 174,630-deck training set. The 173,667-deck control omitted those 963 lists. On the shared held-out queries, top-ten inclusion recall was 46.5% with the Y’shtola lists and 46.0% in the control.

The first pass scored a one-for-one mana filter as ramp. It also credited Shrine and Blood-token payoffs without enough support for them. The revised search checks net mana, separates direct interaction from abilities that place counters, and rewards qualifying instants that can be played without an opposing spell to target. The final version includes Dismember and Snuff Out for life payments, Vito and Sanguine Bond to add life loss, and Queza and Frantic Search for the draw engine.

| Checked finalist | Lands | Land subtotal | Nonland subtotal | EDHPowerLevel playability | Simulated Y’shtola by turn four |
| --- | ---: | ---: | ---: | ---: | ---: |
| Published deck | 39 | $112.94 | $109.58 | 75.9% | 81.4% |
| Fixed-spell mana comparison | 39 | $105.94 | $109.58 | 75.9% | 81.2% |
| Model without Y’shtola lists, same lands as the published deck | 39 | $112.94 | $114.43 | 77.4% | 83.1% |

The selection favors the 22 proactive qualifying off-turn casts in the published deck over the control’s 13. The published mana base had slightly better unrestricted blue and white coverage than the fixed-spell comparison and cost within $3.36 of the nonlands. The benchmark used 12,000 simulated openings per list with land drops and early ramp. The exact published list checked at estimated Bracket 2. Its hash starts with `65da04cf1d1b`. [Published Y’shtola deck](https://cinacho23.github.io/edhrec-affinity/#/synergy-deck?commander=yshtola-nights-blessed)

## Silas Renn and Rograkh adaptation

Rograkh can tap to power another permanent’s ability or be sacrificed to a creature outlet. His first cast costs zero, he has red color for Mox Amber, and his zero power limits outlets that scale with power. Silas needs to deal combat damage to a player. He can then cast the targeted artifact card from your graveyard that turn, paying its costs and following normal timing. Count low-cost castable artifacts, ways to get them into the graveyard, ways for Silas to connect, and payoffs for replaying or accumulating artifacts. [Commander Legends release notes](https://magic.wizards.com/en/news/feature/commander-legends-release-notes-2020-11-06)

Use `python -m edhrec_affinity.synergy_prototype.commander_pipeline --commander rograkh-son-of-rohgahh-silas-renn-seeker-adept` with the collector checkpoint for this exact pair. The control omits their lists and retains decks with either commander in a different pairing. Generate from both rankers with `generate --check`. The opening benchmark draws from 98 cards and measures when the deck can pay for Silas with its opening lands and early ramp. The mana allocator uses the completed nonland subtotal and excludes the three original Grixis duals. The published report lives in `frontend/src/generated/synergy-deck-rog-silas.json`.

### Silas Renn and Rograkh run record

The November 28 gallery indexed 9,305 public decks. All 670 previews in the collected sample produced distinct, eligible deck lists. The matched models trained on 173,554 decks with the pair’s lists and 172,884 decks in the control. Both had 51,841 parameters. Top-ten recall on the shared held-out inclusion queries was 42.7% with the pair’s lists and 44.2% in the control. [EDHREC deck gallery](https://edhrec.com/commanders/rograkh-son-of-rohgahh-silas-renn-seeker-adept/decks)

The first pass checked at 83.5%–90.2% playability and selected many artifacts that did the same job. The revised search limits redundant consumables and rewards effects that trigger on other artifacts, repeatable damage, and finishes that scale with the board. It checks what each sacrifice outlet can get from a zero-power, zero-mana-value creature. The mana allocator also gives utility to artifact lands that contribute to those payoffs.

| Checked finalist | Lands | Land subtotal | Nonland subtotal | EDHPowerLevel playability | Simulated Silas by turn three |
| --- | ---: | ---: | ---: | ---: | ---: |
| Published deck | 35 | $107.64 | $103.60 | 77.2% | 78.3% |
| Same spells with Haunted Ridge replacing Mount Doom | 35 | $101.44 | $103.60 | 77.2% | 78.2% |
| Model without the pair’s lists | 35 | $70.32 | $71.32 | 76.5% | 77.6% |

The published list has 11 consumable replay targets to the control’s eight and adds Aether Spellbomb, Mnemonic Sphere, and Deadly Rollick. Its lands include Great Furnace, Seat of the Synod, and Vault of Whispers, with Mount Doom providing untapped black or red mana. The land-only alternative costs $6.20 less and had essentially the same opening results across 12,000 simulated draws per deck. All three finalists checked at estimated Bracket 2. The published list’s hash starts with `ec46788a0a9e`. [Published Silas Renn and Rograkh deck](https://cinacho23.github.io/edhrec-affinity/#/synergy-deck?commander=rograkh-son-of-rohgahh-silas-renn-seeker-adept)
