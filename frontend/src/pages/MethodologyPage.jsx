export default function MethodologyPage() {
  return (
    <div className="page page--narrow">
      <section className="page-header">
        <p className="eyebrow">Methodology</p>
        <h1>How commander tag affinity is measured</h1>

        <p>
          Affinity measures how strongly a commander is associated with an
          EDHREC tag. We keep the observed percentage and also estimate an
          adjusted percentage that accounts for sample size. The z-score
          compares that adjusted estimate with the tag’s usual variation
          between commanders. Build Rarity expresses how uncommon the tagged
          build is within this particular commander’s decks.
        </p>
      </section>

      <section className="method-section">
        <h2>1. Observed affinity</h2>

        <p>
          Each row represents one commander-tag pair. Let k be the number of
          tagged decks and n the commander’s total deck count. Raw affinity is:
        </p>
        <pre><code>raw affinity = k / n</code></pre>
        <p>
          A commander with 40 tagged decks out of 200 has 20% raw affinity.
          So does a commander with 4,000 tagged decks out of 20,000. Their
          observed percentages match, but the second estimate has more data
          behind it.
        </p>
      </section>

      <section className="method-section">
        <h2>2. A learned baseline for each tag</h2>

        <p>
          We fit an empirical Bayes beta-binomial model separately for each
          tag. It treats each commander’s underlying affinity as a probability
          drawn from a Beta distribution, then models its tagged deck count
          using that probability and its total deck count.
        </p>
        <pre><code>{`commander affinity θ ~ Beta(α, β)
tagged decks k | θ ~ Binomial(n, θ)
baseline μ = α / (α + β)
prior strength κ = α + β`}</code></pre>
        <p>
          The baseline μ and strength κ are learned by maximizing the
          beta-binomial marginal likelihood. Each observed commander contributes
          one count pair; n describes the amount of evidence behind its
          percentage. The fit captures variation between commanders as well as
          sampling uncertainty within each commander.
        </p>
        <p>
          The reference group contains observed commander-tag rows with at least
          200 total decks. There is no minimum tagged-deck count for fitting.
          Missing tag rows are not treated as zero: absence may mean the tag was
          not reported or collected. This baseline describes the reported
          reference group, rather than the share of all Commander decks carrying
          the tag. It is neither an equal-weight average of raw percentages nor
          a pooled percentage of all decks.
        </p>
      </section>

      <section className="method-section">
        <h2>3. Adjusted affinity</h2>

        <pre><code>adjusted affinity = (k + κμ) / (n + κ)</code></pre>
        <p>
          This is the posterior mean: a weighted average of the observed
          percentage and the learned tag baseline. Small samples move further
          toward the baseline. Large samples stay closer to their observed
          percentage. κ acts like a number of baseline decks and is learned
          separately for each tag; it is not a fixed popularity penalty.
        </p>
        <p>
          For illustration, if a tag’s fitted baseline were 10% and its strength
          were 200, the 40-of-200 row would adjust from 20% to 15%. The
          4,000-of-20,000 row would adjust to about 19.90%. These are example
          parameters, not a fixed baseline used for every tag.
        </p>
        <p>
          Sharing evidence this way is called partial pooling. See Stan’s{" "}
          <a href="https://mc-stan.org/learn-stan/case-studies/pool-binary-trials.html" target="_blank" rel="noreferrer">
            explanation of partial pooling for binary trials
          </a>{" "}
          for the statistical motivation.
        </p>
      </section>

      <section className="method-section">
        <h2>4. The specialization z-score</h2>

        <pre><code>{`tag baseline spread σ = sqrt(μ(1 − μ) / (κ + 1))
z = (adjusted affinity − μ) / σ`}</code></pre>
        <p>
          σ is the fitted spread of underlying affinities between commanders,
          rather than the observed sample standard deviation of raw percentages.
          A score of 2 means the adjusted affinity sits two fitted baseline
          standard deviations above the tag baseline. Positive scores indicate
          above-baseline specialization; negative scores indicate below-baseline
          affinity.
        </p>
        <p>
          Deck count affects how much we trust the affinity estimate. The score
          still measures specialization, so it does not increase indefinitely
          just because a commander has more decks. It is not a significance
          test, a p-value, a win-rate estimate, or a measure of deck power. The
          fitted Beta distribution need not be normal, so familiar normal-curve
          probabilities do not apply to these z-scores.
        </p>
      </section>

      <section className="method-section">
        <h2>5. The 95% range</h2>

        <pre><code>{`posterior affinity ~ Beta(k + κμ, n − k + κ(1 − μ))
95% range = posterior 2.5th to 97.5th percentiles`}</code></pre>
        <p>
          This is a Bayesian credible interval for the commander’s underlying
          tag affinity under the model. Smaller samples generally have wider
          ranges. It is conditional on the fitted baseline and strength, which
          are treated as fixed; it does not include uncertainty in those learned
          parameters or uncertainty about EDHREC’s tagging and data coverage.
          A narrow range therefore does not establish an unbiased or perfectly
          accurate estimate.
        </p>
      </section>

      <section className="method-section">
        <h2>6. Build Rarity</h2>

        <pre><code>build rarity N = 1 / adjusted affinity</code></pre>
        <p>
          Build Rarity displays the same adjusted affinity as “≈ 1 in N.”
          Affinity is a fraction in this formula: 20% is 0.20 and gives
          approximately 1 in 5; 2% gives 1 in 50; 0.2% gives 1 in 500. A higher
          N means the tag is less common within this commander’s recorded deck
          population. It is a way to explore unusual builds, without requiring
          a negative z-score.
        </p>
        <p>
          The two metrics answer different questions. Build Rarity asks how
          uncommon this tag is for this commander. The z-score compares the
          commander’s affinity with other commanders for the same tag. A tag
          can be rare for a commander and still have a positive z-score if it
          is even rarer among other commanders.
        </p>
        <p>
          This is a reciprocal of the existing adjusted estimate, not a new
          statistical model, a prediction of future decks, or a waiting time
          until another tagged deck appears. The existing 95% affinity range
          describes uncertainty in the underlying share; a wide range also
          means the rarity estimate is less precise. Rarity does not establish
          that a build is viable, powerful, or a good fit for the commander.
          Even a commander’s leading tag can have a small reported share, so
          there is no universal rarity cutoff for calling a build off-meta.
        </p>
        <p>
          Explicit model fallbacks retain raw affinity as their estimate and
          show “unadjusted.” Older exports without an adjusted estimate show —,
          even when raw affinity is available. Missing tag rows and zero or
          missing usable estimates also show —; they are not treated as
          infinitely rare builds. Tags can overlap, and unreported tags are
          not filled with zeros.
        </p>
        <p>
          The Quirky Builds report selects the highest Build Rarity values per
          theme, or per theme and commander bracket. It includes observed
          builds regardless of whether their z-score is positive, negative, or
          unavailable. Its default filters require 200 total decks, 5 tagged
          decks, and rarity of at least 1 in 50 (an adjusted share of at most
          2%). These are editable starting filters, not a universal definition
          of off-meta. Filters apply before selecting the top builds. Ties use
          tagged decks, then total decks, then commander name and slug.
          Brackets describe the commander’s existing recommendation; they do
          not rate the power of the unusual build.
        </p>
      </section>

      <section className="method-section">
        <h2>7. Fit safeguards and fallbacks</h2>

        <p>
          A tag needs at least five distinct reference commanders and varying
          affinity percentages for a usable fit. Numerical optimization keeps
          μ between 10⁻⁹ and 1 − 10⁻⁹ and κ between 0.001 and 1,000,000.
          Fits that fail, produce invalid values, or reach the upper strength
          bound are rejected.
        </p>
        <p>
          Sparse tags, invariant tags, and rejected fits keep the observed
          affinity and previous raw-affinity z-score:
        </p>
        <pre><code>fallback z = (raw affinity − raw tag mean) / raw tag sample standard deviation</code></pre>
        <p>
          These rows show “unadjusted,” with no 95% range. The fallback mean and
          sample standard deviation use all observed rows for the tag, as in
          the previous algorithm. A zero standard deviation has no defined
          z-score. Older exports without adjusted estimates show — instead of
          implying that the model was fitted.
        </p>
      </section>

      <section className="method-section">
        <h2>8. Rankings and display filters</h2>

        <p>
          Within-tag z-score ranks and percentiles use the revised
          specialization score, or the previous score when the tag falls back.
          Separate rankings by raw affinity and tagged-deck count retain their
          original meanings. Percentiles describe the observed comparison
          group; they are not probabilities that a commander is the best choice.
        </p>
        <p>
          The default leaderboard and tag filters require at least 200 total
          decks and 5 tagged decks. These display filters apply after fitting
          and ranking. Changing them hides or shows rows without learning a new
          baseline or recalculating the score. In particular, the 5-tagged-deck
          display cutoff does not remove rows from the model’s reference group.
        </p>
        <p>
          Commander detail pages show only tags with at least 5 tagged decks.
          Their tag counts and strongest and largest tag summaries use the same
          minimum. Commanders remain visible even when none of their tags meet
          this minimum.
        </p>
        <p>
          “Rank in tag” compares the commander with all reported commanders for
          that tag, before page filters. It is not a rank within the selected
          set or bracket. Theme Report also has a separate rank within the
          filtered theme or theme-and-bracket group.
        </p>
      </section>

      <section className="method-section">
        <h2>9. Brackets and cEDH</h2>

        <p>
          The upgraded affinity z-score is used consistently for rankings,
          bracket suggestions, and theme eligibility. The numeric bracket
          cutoffs remain 0, 0.05, 0.95, and 1.05, including the existing in-flux
          bands. cEDH is evaluated first; when it does not qualify for Bracket 4
          or higher, Aggro, Control, Midrange, Tempo, and Combo are considered.
          Ordinary themes still require a theme z-score of at least 1.05.
          cEDH and those five archetype themes use only the bracket rules.
          The score inputs have changed, so commanders can receive different
          suggestions even though the numeric cutoffs are unchanged.
        </p>
        <p>
          A bracket label is our suggested building bracket and recommended
          ceiling, inferred from observed commander-tag associations. These
          cutoffs are recommendation rules rather than a calibrated measure of
          deck power. A particular deck’s strength and appropriate play
          experience depend on its card choices, construction, and intent.
          Using the same upgraded score throughout the site makes the rules
          consistent; it does not mathematically guarantee a power ceiling.
        </p>
        <p>
          Affinity and Build Rarity columns on Sets describe the tag named in
          each row. On Brackets they describe the deciding archetype or cEDH tag; if there
          is no deciding tag, those values are unavailable. Theme Brackets and
          Theme Report show the selected theme’s affinity. Their theme z-score
          and Bracket Z can differ because they describe different tags.
          Bracket Z is the deciding tag’s upgraded affinity z-score, not a
          separate measure of deck power. The unadjusted fallbacks described
          above remain explicit; missing scores are unavailable and do not
          qualify for a score threshold.
        </p>
        <p>
          cEDH is represented as a normalized tag but comes from a special
          filtered source route instead of the normal commander tag list. Its
          source type remains available for auditing, and its affinity model
          uses only its observed reference rows.
        </p>
      </section>

      <section className="method-section">
        <h2>10. Snapshot trends</h2>

        <p>
          Trends compare consecutive processed snapshots. A change in affinity
          model version makes z-scores and their ranks incomparable, so z-score
          and rank changes across that boundary are unavailable.
          These changes are also unavailable when a tag switches between a
          fitted model and a fallback.
          Raw affinity and deck-count changes remain comparable when both
          snapshots contain the needed counts. New or removed pairs do not have
          a complete before-and-after comparison.
        </p>
      </section>

      <section className="method-section">
        <h2>11. Interpretation and data limits</h2>

        <p>
          The model estimates associations within the collected EDHREC data.
          It does not infer missing tags, establish causation, or measure
          performance in games. Tag definitions, reported rows, deck overlap,
          source filters, and coverage can affect results. The binomial model
          also approximates deck observations as independent trials.
        </p>
        <p>
          This project provides original statistical analysis and links back to
          EDHREC where appropriate. Its estimates should support exploration
          alongside decklists and gameplay context.
        </p>
      </section>
    </div>
  );
}
