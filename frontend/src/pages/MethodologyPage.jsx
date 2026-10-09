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
          between commanders.
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
        <h2>6. Fit safeguards and fallbacks</h2>

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
        <h2>7. Rankings and display filters</h2>

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
      </section>

      <section className="method-section">
        <h2>8. Brackets and cEDH</h2>

        <p>
          Revised affinity scores rank the Leaderboard, Tag Explorer, and Theme
          Report. Bracket classification and existing theme eligibility cutoffs
          continue to use the previous raw-affinity z-score and unchanged
          thresholds. Affinity should not be read as evidence that a commander
          or a particular deck belongs in a bracket.
        </p>
        <p>
          cEDH is represented as a normalized tag but comes from a special
          filtered source route instead of the normal commander tag list. Its
          source type remains available for auditing, and its affinity model
          uses only its observed reference rows.
        </p>
      </section>

      <section className="method-section">
        <h2>9. Snapshot trends</h2>

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
        <h2>10. Interpretation and data limits</h2>

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
