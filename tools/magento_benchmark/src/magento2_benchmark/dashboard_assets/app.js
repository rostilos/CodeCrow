// Packaged as a zero-build, auditable dashboard asset.
(() => {
  "use strict";

  const state = {
    data: null,
    configuration: null,
    selectedCaseId: null,
  };

  const elements = {};

  document.addEventListener("DOMContentLoaded", () => {
    [
      "load-status",
      "dashboard",
      "provenance",
      "configuration-select",
      "selected-models",
      "metric-cards",
      "counts-line",
      "comparison-body",
      "pairwise-section",
      "pairwise-body",
      "size-chart",
      "case-select",
      "case-summary",
      "gold-list",
      "candidate-list",
      "assignment-section",
      "assignment-list",
      "methodology-status",
      "methodology-limitations",
    ].forEach((id) => {
      elements[id] = document.getElementById(id);
    });

    elements["configuration-select"].addEventListener("change", (event) => {
      chooseConfiguration(event.target.value);
    });
    elements["case-select"].addEventListener("change", (event) => {
      state.selectedCaseId = event.target.value;
      renderCase();
    });

    loadData();
  });

  async function loadData() {
    try {
      const response = await fetch("data.json", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`data.json returned HTTP ${response.status}`);
      }
      const data = await response.json();
      if (!isObject(data) || !Array.isArray(data.configurations)) {
        throw new Error("data.json does not contain a configurations array");
      }
      state.data = data;
      renderProvenance();
      renderMethodology();
      renderConfigurationOptions();
      renderComparison();
      renderPairwise();

      if (data.configurations.length === 0) {
        throw new Error("the metrics artifact has no configurations to display");
      }

      chooseConfiguration(configurationId(data.configurations[0], 0));
      elements["load-status"].hidden = true;
      elements.dashboard.hidden = false;
    } catch (error) {
      elements["load-status"].classList.add("error");
      elements["load-status"].textContent =
        `Unable to load benchmark data: ${error.message}. ` +
        "Serve this directory with a local HTTP server instead of opening index.html directly.";
    }
  }

  function renderMethodology() {
    const methodology = isObject(state.data.methodology) ? state.data.methodology : {};
    const artifactReady = methodology.artifactIntegrityReady === true;
    const protocolReady = methodology.publicationProtocolReady === true;
    const analysisBound = methodology.analysisArtifactsBound === true;
    elements["methodology-status"].textContent = artifactReady
      ? (
        "Artifact-integrity gate: PASSED. Publication protocol: " +
        (protocolReady ? "PASSED. " : "NOT READY. ") +
        "This dashboard does not claim paper readiness."
      )
      : (
        "Artifact-integrity gate: NOT READY. Publication protocol: NOT READY. " +
        "These results are diagnostic and must not support publication claims. " +
        (analysisBound
          ? "Analysis artifacts are bound."
          : "Exact analysis-run artifacts were not supplied to metrics.")
      );
    elements["methodology-status"].classList.toggle("not-ready", !artifactReady);
    clear(elements["methodology-limitations"]);
    const limitations = Array.isArray(methodology.limitations)
      ? methodology.limitations
      : [];
    const artifactFailures = Array.isArray(methodology.artifactIntegrityGateFailures)
      ? methodology.artifactIntegrityGateFailures.map(
        (failure) => `Artifact-integrity gate: ${failure}`,
      )
      : [];
    const protocolFailures = Array.isArray(methodology.publicationProtocolGateFailures)
      ? methodology.publicationProtocolGateFailures.map(
        (failure) => `Publication-protocol gate: ${failure}`,
      )
      : [];
    const controls = isObject(methodology.protocolControls)
      ? Object.entries(methodology.protocolControls).map(
        ([name, control]) => (
          `Protocol control ${name}: ${
            isObject(control) && control.status ? control.status : "not reported"
          }`
        ),
      )
      : [];
    [...controls, ...artifactFailures, ...protocolFailures, ...limitations].forEach((limitation) => {
      elements["methodology-limitations"].append(
        node("li", "", String(limitation)),
      );
    });
  }

  function renderProvenance() {
    clear(elements.provenance);
    const corpus = isObject(state.data.corpus) ? state.data.corpus : {};
    const entries = [
      ["Generated", formatDate(state.data.generatedAt)],
      ["Corpus", firstText(corpus.digest, corpus.corpusDigest, corpus.id, "not reported")],
      ["Cases", firstNumber(corpus.caseCount, corpus.cases, state.data.caseCount)],
    ];

    entries.forEach(([label, value]) => {
      if (value === null || value === undefined || value === "") {
        return;
      }
      const wrapper = node("div", "provenance-item");
      wrapper.append(node("span", "provenance-label", label));
      wrapper.append(node("span", "provenance-value", String(value)));
      elements.provenance.append(wrapper);
    });
  }

  function renderConfigurationOptions() {
    clear(elements["configuration-select"]);
    state.data.configurations.forEach((config, index) => {
      const option = document.createElement("option");
      option.value = configurationId(config, index);
      option.textContent = configurationLabel(config, index);
      elements["configuration-select"].append(option);
    });
  }

  function chooseConfiguration(id) {
    const configurations = state.data.configurations;
    const index = configurations.findIndex(
      (config, position) => configurationId(config, position) === id,
    );
    state.configuration = configurations[index >= 0 ? index : 0];
    elements["configuration-select"].value = configurationId(
      state.configuration,
      index >= 0 ? index : 0,
    );
    state.selectedCaseId = null;
    renderOverview();
    renderSizeChart();
    renderCaseOptions();
    renderCase();
  }

  function renderOverview() {
    const config = state.configuration;
    const primary = metricBlock(config);
    const micro = isObject(primary.micro) ? primary.micro : primary;
    const macro = isObject(primary.macro) ? primary.macro : {};
    const secondary = isObject(config.secondary) ? config.secondary : {};
    const allCases = isObject(secondary.allCases) ? secondary.allCases : {};
    const development = isObject(secondary.development) ? secondary.development : {};
    const allCasesMicro = isObject(allCases.micro) ? allCases.micro : {};
    const developmentMicro = isObject(development.micro) ? development.micro : {};
    const confirmatoryCoverage = isObject(config.confirmatoryCoverage)
      ? config.confirmatoryCoverage
      : config.coverage;

    elements["selected-models"].textContent =
      `${firstText(config.analysisModel, config.model, "Analysis model not reported")} · ` +
      `${firstText(config.judgeModel, "Judge model not reported")}`;

    const cards = [
      [
        "Sealed precision",
        formatRate(micro.precision),
        intervalText(
          firstValue(
            micro.precisionInterval,
            micro.confidenceInterval95 && micro.confidenceInterval95.precision,
          ),
        ),
      ],
      [
        "Sealed recall",
        formatRate(micro.recall),
        intervalText(
          firstValue(
            micro.recallInterval,
            micro.confidenceInterval95 && micro.confidenceInterval95.recall,
          ),
        ),
      ],
      [
        "Sealed F1",
        formatRate(firstNumber(micro.f1, micro.f1Score)),
        intervalText(
          firstValue(
            micro.f1Interval,
            micro.confidenceInterval95 && micro.confidenceInterval95.f1,
          ),
        ),
      ],
      [
        "Sealed coverage",
        formatCoverage(confirmatoryCoverage),
        coverageDetail(confirmatoryCoverage),
      ],
      [
        "All-50 F1",
        formatRate(firstNumber(allCasesMicro.f1, allCasesMicro.f1Score)),
        "Secondary aggregate",
      ],
      [
        "Development F1",
        formatRate(firstNumber(developmentMicro.f1, developmentMicro.f1Score)),
        "Secondary aggregate",
      ],
      [
        "Sealed macro F1",
        formatRate(firstNumber(macro.f1, macro.f1Score)),
        "Mean across scored sealed cases",
      ],
    ];

    clear(elements["metric-cards"]);
    cards.forEach(([label, value, detail]) => {
      const card = node("article", "metric-card");
      card.append(node("h3", "metric-label", label));
      card.append(node("p", "metric-value", value));
      if (detail) {
        card.append(node("p", "metric-detail", detail));
      }
      elements["metric-cards"].append(card);
    });

    const counts = metricCounts(micro);
    elements["counts-line"].textContent =
      `Confirmatory sealed reference-set counts: ${formatCount(counts.tp)} matched (TP), ` +
      `${formatCount(counts.fp)} unmatched candidates (reference-set FP), and ` +
      `${formatCount(counts.fn)} unmatched reviewer issues (FN).`;
  }

  function renderComparison() {
    clear(elements["comparison-body"]);
    state.data.configurations.forEach((config, index) => {
      const micro = microMetrics(config);
      const counts = metricCounts(micro);
      const row = document.createElement("tr");
      const values = [
        firstText(config.analysisModel, config.model, `Configuration ${index + 1}`),
        firstText(config.judgeModel, "not reported"),
        formatCoverage(
          isObject(config.confirmatoryCoverage)
            ? config.confirmatoryCoverage
            : config.coverage,
        ),
        formatRate(micro.precision),
        formatRate(micro.recall),
        formatRate(firstNumber(micro.f1, micro.f1Score)),
        `${formatCount(counts.tp)} / ${formatCount(counts.fp)} / ${formatCount(counts.fn)}`,
      ];
      values.forEach((value) => row.append(node("td", "", String(value))));
      elements["comparison-body"].append(row);
    });
  }

  function renderPairwise() {
    const comparisons = Array.isArray(state.data.pairwiseComparisons)
      ? state.data.pairwiseComparisons
      : [];
    clear(elements["pairwise-body"]);
    elements["pairwise-section"].hidden = comparisons.length === 0;

    comparisons.forEach((comparison) => {
      const row = document.createElement("tr");
      const delta = firstNumber(
        comparison.microDeltaOnCommonCases
          && comparison.microDeltaOnCommonCases.f1,
        comparison.f1Delta,
        comparison.deltaF1,
        comparison.macroPerCaseF1Delta,
        comparison.microF1DeltaOnCommonCases,
        comparison.delta && comparison.delta.f1,
      );
      const interval = firstValue(
        comparison.microDeltaConfidenceInterval95
          && comparison.microDeltaConfidenceInterval95.f1,
        comparison.f1Interval,
        comparison.macroPerCaseF1DeltaConfidenceInterval95,
        comparison.confidenceInterval,
        comparison.interval,
      );
      const values = [
        firstText(
          comparison.configA,
          comparison.leftConfigId,
          comparison.baselineConfigId,
          "not reported",
        ),
        firstText(
          comparison.configB,
          comparison.rightConfigId,
          comparison.candidateConfigId,
          "not reported",
        ),
        formatCount(
          firstNumber(
            comparison.commonCases,
            comparison.commonScoredCases,
            comparison.caseCount,
            comparison.n,
          ),
        ),
        formatSignedRate(delta),
        intervalText(interval) || "not reported",
      ];
      values.forEach((value) => row.append(node("td", "", String(value))));
      elements["pairwise-body"].append(row);
    });
  }

  function renderSizeChart() {
    clear(elements["size-chart"]);
    const strata = normaliseStrata(
      isObject(state.configuration.strata) ? state.configuration.strata.sizeBand : null,
    );

    if (strata.length === 0) {
      elements["size-chart"].append(
        node("p", "empty-state", "No size-band measurements were reported."),
      );
      return;
    }

    const legend = node("div", "chart-legend");
    [
      ["Precision", "precision"],
      ["Recall", "recall"],
      ["F1", "f1"],
    ].forEach(([label, className]) => {
      const item = node("span", "legend-item");
      item.append(node("span", `legend-swatch ${className}`));
      item.append(document.createTextNode(label));
      legend.append(item);
    });
    elements["size-chart"].append(legend);

    strata.forEach((stratum, index) => {
      const metrics = stratumMetrics(stratum.value);
      const row = node("article", "band-row");
      const heading = node("div", "band-heading");
      heading.append(node("h3", "", humanize(stratum.label || `Band ${index + 1}`)));
      const caseCount = firstNumber(
        stratum.value.caseCount,
        stratum.value.count,
        stratum.value.counts && stratum.value.counts.cases,
      );
      if (caseCount !== null) {
        heading.append(node("p", "", `${caseCount} scored case${caseCount === 1 ? "" : "s"}`));
      }
      row.append(heading);

      const tracks = node("div", "band-tracks");
      [
        ["Precision", "precision", metrics.precision],
        ["Recall", "recall", metrics.recall],
        ["F1", "f1", firstNumber(metrics.f1, metrics.f1Score)],
      ].forEach(([label, className, value]) => {
        const track = node("div", "band-track");
        const name = node("span", "band-metric-name", label);
        const meter = node("div", "meter");
        meter.setAttribute("role", "meter");
        meter.setAttribute("aria-label", `${humanize(stratum.label)} ${label}`);
        meter.setAttribute("aria-valuemin", "0");
        meter.setAttribute("aria-valuemax", "100");
        meter.setAttribute("aria-valuenow", String(Math.round(toRate(value) * 100)));
        const fill = node("span", `meter-fill ${className}`);
        fill.style.width = `${toRate(value) * 100}%`;
        meter.append(fill);
        const number = node("span", "band-metric-value", formatRate(value));
        track.append(name, meter, number);
        tracks.append(track);
      });
      row.append(tracks);
      elements["size-chart"].append(row);
    });
  }

  function renderCaseOptions() {
    const cases = configurationCases();
    clear(elements["case-select"]);
    cases.forEach((caseResult, index) => {
      const option = document.createElement("option");
      option.value = caseId(caseResult, index);
      option.textContent = caseLabel(caseResult, index);
      elements["case-select"].append(option);
    });
    elements["case-select"].disabled = cases.length === 0;
    if (cases.length > 0) {
      state.selectedCaseId = caseId(cases[0], 0);
      elements["case-select"].value = state.selectedCaseId;
    }
  }

  function renderCase() {
    const cases = configurationCases();
    const position = cases.findIndex(
      (caseResult, index) => caseId(caseResult, index) === state.selectedCaseId,
    );
    const caseResult = cases[position >= 0 ? position : 0];
    clear(elements["case-summary"]);
    clear(elements["gold-list"]);
    clear(elements["candidate-list"]);
    clear(elements["assignment-list"]);

    if (!caseResult) {
      elements["case-summary"].append(
        node("p", "empty-state", "No per-case results were reported."),
      );
      elements["assignment-section"].hidden = true;
      return;
    }

    state.selectedCaseId = caseId(caseResult, position >= 0 ? position : 0);
    elements["case-select"].value = state.selectedCaseId;
    elements["assignment-section"].hidden = false;

    renderCaseSummary(caseResult);
    const goldIssues = issueArray(
      caseResult.goldIssues,
      caseResult.gold,
      caseResult.referenceIssues,
    );
    const candidates = issueArray(
      caseResult.candidateFindings,
      caseResult.findings,
      caseResult.codecrowFindings,
    );
    const assignments = issueArray(caseResult.assignments, caseResult.matches);
    const verdicts = verdictMap(caseResult);

    const goldAssignments = assignmentIndex(assignments, "gold");
    const candidateAssignments = assignmentIndex(assignments, "candidate");

    renderIssues(
      elements["gold-list"],
      goldIssues,
      "gold",
      goldAssignments,
      verdicts,
    );
    renderIssues(
      elements["candidate-list"],
      candidates,
      "candidate",
      candidateAssignments,
      verdicts,
    );
    renderAssignments(assignments);
  }

  function renderCaseSummary(caseResult) {
    const chips = node("div", "case-chips");
    [
      firstText(caseResult.sizeBand, caseResult.band),
      firstText(caseResult.partition),
      firstText(caseResult.status),
    ]
      .filter(Boolean)
      .forEach((value) => chips.append(node("span", "chip neutral", humanize(value))));

    const metrics = stratumMetrics(
      firstValue(caseResult.primary, caseResult.metrics, caseResult),
    );
    const counts = metricCounts(metrics);
    const title = node("div", "case-title");
    title.append(node("h3", "", caseLabel(caseResult, 0)));
    const pathCount = firstNumber(caseResult.changedFileCount, caseResult.fileCount);
    const detailParts = [
      pathCount === null ? "" : `${pathCount} changed files`,
      `P ${formatRate(metrics.precision)}`,
      `R ${formatRate(metrics.recall)}`,
      `F1 ${formatRate(firstNumber(metrics.f1, metrics.f1Score))}`,
      `TP/FP/FN ${formatCount(counts.tp)}/${formatCount(counts.fp)}/${formatCount(counts.fn)}`,
    ].filter(Boolean);
    title.append(node("p", "", detailParts.join(" · ")));
    elements["case-summary"].append(title, chips);
  }

  function renderIssues(container, issues, kind, assignmentLookup, verdicts) {
    if (issues.length === 0) {
      container.append(
        node(
          "p",
          "empty-state",
          kind === "gold"
            ? "No reviewer reference issues were reported for this case."
            : "No CodeCrow candidate findings were reported for this case.",
        ),
      );
      return;
    }

    issues.forEach((issue, index) => {
      const id = issueId(issue, kind, index);
      const assignment = assignmentLookup.get(id);
      const card = node("article", "issue-card");
      const heading = node("div", "issue-card-heading");
      const title = firstText(
        issue.title,
        issue.summary,
        issue.message,
        issue.expectedIssue && issue.expectedIssue.title,
        `${kind === "gold" ? "Reviewer issue" : "Candidate finding"} ${index + 1}`,
      );
      heading.append(node("h4", "", title));

      if (kind === "gold") {
        heading.append(
          node(
            "span",
            assignment ? "chip matched" : "chip fn",
            assignment ? "Matched" : "FN · unmatched reference",
          ),
        );
      } else if (assignment) {
        heading.append(node("span", "chip matched", "Matched"));
      } else {
        heading.append(node("span", "chip fp", "Reference-set FP"));
      }
      card.append(heading);

      const location = issueLocation(issue);
      if (location) {
        card.append(node("p", "issue-location", location));
      }

      const labels = [
        firstText(issue.category, issue.expectedIssue && issue.expectedIssue.category),
        firstText(issue.severity, issue.expectedIssue && issue.expectedIssue.severity),
      ].filter(Boolean);
      if (labels.length > 0) {
        const metadata = node("p", "issue-metadata");
        labels.forEach((label) => metadata.append(node("span", "chip neutral", humanize(label))));
        card.append(metadata);
      }

      const body = firstText(
        issue.body,
        issue.reviewComment,
        issue.description,
        issue.explanation,
        issue.expectedIssue && issue.expectedIssue.requiredChange,
      );
      if (body) {
        card.append(node("p", "issue-body", body));
      }

      if (kind === "candidate" && !assignment) {
        const verdict = verdicts.get(id);
        const verdictText = novelVerdictText(verdict);
        if (verdictText) {
          card.append(node("p", "novel-verdict", verdictText));
        }
      }

      container.append(card);
    });
  }

  function renderAssignments(assignments) {
    if (assignments.length === 0) {
      elements["assignment-list"].append(
        node("p", "empty-state", "No one-to-one matches were assigned in this case."),
      );
      return;
    }

    assignments.forEach((assignment, index) => {
      const goldId = assignmentEndpoint(assignment, "gold") || `gold ${index + 1}`;
      const candidateId =
        assignmentEndpoint(assignment, "candidate") || `candidate ${index + 1}`;
      const item = node("article", "assignment-card");
      item.append(
        node("h4", "", `${goldId} ↔ ${candidateId}`),
        node(
          "p",
          "assignment-score",
          assignmentScoreLabel(assignment),
        ),
      );
      const rationale = firstText(
        assignment.rationale,
        assignment.reason,
        assignment.explanation,
        assignment.judgment && assignment.judgment.rationale,
      );
      if (rationale) {
        item.append(node("p", "", rationale));
      }
      elements["assignment-list"].append(item);
    });
  }

  function configurationCases() {
    return Array.isArray(state.configuration && state.configuration.cases)
      ? state.configuration.cases
      : [];
  }

  function configurationId(config, index) {
    return String(firstText(config.configId, config.id, `configuration-${index + 1}`));
  }

  function configurationLabel(config, index) {
    const analysis = firstText(config.analysisModel, config.model, `Configuration ${index + 1}`);
    const judge = firstText(config.judgeModel, "judge not reported");
    return `${analysis} / ${judge}`;
  }

  function caseId(caseResult, index) {
    return String(firstText(caseResult.caseId, caseResult.id, `case-${index + 1}`));
  }

  function caseLabel(caseResult, index) {
    const id = caseId(caseResult, index);
    const prNumber = firstNumber(
      caseResult.prNumber,
      caseResult.sourcePrNumber,
      caseResult.sourcePr && caseResult.sourcePr.number,
    );
    return prNumber === null ? id : `${id} · source PR #${prNumber}`;
  }

  function metricBlock(config) {
    return isObject(config.primary)
      ? config.primary
      : isObject(config.metrics)
        ? config.metrics
        : {};
  }

  function microMetrics(config) {
    const primary = metricBlock(config);
    return isObject(primary.micro) ? primary.micro : primary;
  }

  function stratumMetrics(value) {
    if (!isObject(value)) {
      return {};
    }
    if (isObject(value.primary)) {
      return isObject(value.primary.micro) ? value.primary.micro : value.primary;
    }
    if (isObject(value.micro)) {
      return value.micro;
    }
    if (isObject(value.metrics)) {
      return isObject(value.metrics.micro) ? value.metrics.micro : value.metrics;
    }
    return value;
  }

  function metricCounts(metrics) {
    const counts = isObject(metrics.counts) ? metrics.counts : {};
    return {
      tp: firstNumber(counts.tp, counts.truePositive, metrics.tp),
      fp: firstNumber(
        counts.fp,
        counts.referenceSetFalsePositive,
        metrics.fp,
        metrics.referenceSetFalsePositive,
      ),
      fn: firstNumber(counts.fn, counts.falseNegative, metrics.fn),
    };
  }

  function normaliseStrata(raw) {
    if (Array.isArray(raw)) {
      return raw
        .filter(isObject)
        .map((value, index) => ({
          label: firstText(value.label, value.name, value.sizeBand, `band-${index + 1}`),
          value,
        }));
    }
    if (isObject(raw)) {
      return Object.entries(raw)
        .filter(([, value]) => isObject(value))
        .map(([label, value]) => ({ label, value }));
    }
    return [];
  }

  function assignmentIndex(assignments, endpoint) {
    const lookup = new Map();
    assignments.forEach((assignment) => {
      const id = assignmentEndpoint(assignment, endpoint);
      if (id) {
        lookup.set(String(id), assignment);
      }
    });
    return lookup;
  }

  function assignmentEndpoint(assignment, endpoint) {
    if (!isObject(assignment)) {
      return "";
    }
    if (endpoint === "gold") {
      return firstText(
        assignment.goldId,
        assignment.goldIssueId,
        assignment.referenceId,
        assignment.gold && assignment.gold.id,
      );
    }
    return firstText(
      assignment.candidateId,
      assignment.findingId,
      assignment.candidateFindingId,
      assignment.candidate && assignment.candidate.id,
    );
  }

  function verdictMap(caseResult) {
    const raw = firstValue(
      caseResult.novelVerdicts,
      caseResult.unmatchedCandidateVerdicts,
      caseResult.novelFindingJudgments,
      caseResult.novelFindings,
    );
    const output = new Map();
    if (Array.isArray(raw)) {
      raw.forEach((verdict, index) => {
        if (!isObject(verdict)) {
          return;
        }
        const id = firstText(
          verdict.candidateId,
          verdict.findingId,
          verdict.id,
          `candidate-${index + 1}`,
        );
        output.set(String(id), verdict);
      });
    } else if (isObject(raw)) {
      Object.entries(raw).forEach(([id, verdict]) => output.set(id, verdict));
    }
    return output;
  }

  function novelVerdictText(verdict) {
    if (verdict === null || verdict === undefined) {
      return "";
    }
    const label = isObject(verdict)
      ? firstText(verdict.verdict, verdict.label, verdict.disposition, verdict.status)
      : String(verdict);
    if (!label) {
      return "";
    }
    const explanation = isObject(verdict)
      ? firstText(verdict.rationale, verdict.reason, verdict.explanation)
      : "";
    return `Novel-finding adjudication: ${humanize(label)}${explanation ? ` — ${explanation}` : ""}`;
  }

  function issueArray(...values) {
    const value = values.find(Array.isArray);
    return value || [];
  }

  function issueId(issue, kind, index) {
    return String(
      firstText(
        issue.id,
        issue.goldId,
        issue.candidateId,
        issue.findingId,
        issue.commentId,
        `${kind}-${index + 1}`,
      ),
    );
  }

  function issueLocation(issue) {
    const anchor = isObject(issue.anchor) ? issue.anchor : {};
    const path = firstText(issue.path, issue.filePath, anchor.path);
    const line = firstNumber(issue.line, issue.startLine, anchor.line, anchor.startLine);
    if (!path) {
      return "";
    }
    return line === null ? path : `${path}:${line}`;
  }

  function assignmentScoreLabel(assignment) {
    const confidence = firstNumber(
      assignment.score,
      assignment.confidence,
      assignment.matchProbability,
      assignment.judgment && assignment.judgment.confidence,
    );
    if (confidence !== null) {
      return `Match confidence: ${formatRate(confidence)}`;
    }
    const weight = firstNumber(assignment.weight);
    return weight === null
      ? "Match score: not reported"
      : `Assignment evidence weight: ${weight.toFixed(3)}`;
  }

  function formatCoverage(coverage) {
    const rate = coverageRate(coverage);
    return rate === null ? "not reported" : formatRate(rate);
  }

  function coverageDetail(coverage) {
    if (!isObject(coverage)) {
      return "";
    }
    const completed = firstNumber(
      coverage.scoredCases,
      coverage.completedCases,
      coverage.evaluatedCases,
      coverage.completed,
    );
    const total = firstNumber(coverage.totalCases, coverage.total);
    if (completed === null || total === null) {
      return "";
    }
    const uncertain = firstNumber(coverage.uncertainCases);
    const uncertaintyText = uncertain && uncertain > 0
      ? `; ${uncertain} excluded as judge-unverifiable`
      : "";
    return `${completed} of ${total} cases evaluated${uncertaintyText}`;
  }

  function coverageRate(coverage) {
    if (typeof coverage === "number") {
      return coverage;
    }
    if (!isObject(coverage)) {
      return null;
    }
    const direct = firstNumber(coverage.rate, coverage.ratio, coverage.coverage);
    if (direct !== null) {
      return direct;
    }
    const completed = firstNumber(
      coverage.scoredCases,
      coverage.completedCases,
      coverage.evaluatedCases,
      coverage.completed,
    );
    const total = firstNumber(coverage.totalCases, coverage.total);
    return completed !== null && total ? completed / total : null;
  }

  function intervalText(interval) {
    if (Array.isArray(interval) && interval.length >= 2) {
      return `95% interval ${formatRate(interval[0])}–${formatRate(interval[1])}`;
    }
    if (isObject(interval)) {
      const low = firstNumber(interval.low, interval.lower, interval.min);
      const high = firstNumber(interval.high, interval.upper, interval.max);
      if (low !== null && high !== null) {
        return `95% interval ${formatRate(low)}–${formatRate(high)}`;
      }
    }
    return "";
  }

  function formatRate(value) {
    const number = numeric(value);
    if (number === null) {
      return "not reported";
    }
    const rate = Math.abs(number) > 1 ? number / 100 : number;
    return new Intl.NumberFormat("en", {
      style: "percent",
      maximumFractionDigits: 1,
    }).format(rate);
  }

  function formatSignedRate(value) {
    const number = numeric(value);
    if (number === null) {
      return "not reported";
    }
    const rate = Math.abs(number) > 1 ? number / 100 : number;
    const formatted = new Intl.NumberFormat("en", {
      style: "percent",
      maximumFractionDigits: 1,
      signDisplay: "always",
    }).format(rate);
    return formatted;
  }

  function toRate(value) {
    const number = numeric(value);
    if (number === null) {
      return 0;
    }
    const rate = Math.abs(number) > 1 ? number / 100 : number;
    return Math.max(0, Math.min(1, rate));
  }

  function formatCount(value) {
    const number = numeric(value);
    return number === null ? "not reported" : String(number);
  }

  function formatDate(value) {
    if (!value) {
      return "not reported";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return new Intl.DateTimeFormat("en", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "UTC",
    }).format(date);
  }

  function humanize(value) {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function firstText(...values) {
    const value = values.find(
      (candidate) =>
        typeof candidate === "string" && candidate.trim().length > 0,
    );
    return value === undefined ? "" : value;
  }

  function firstNumber(...values) {
    for (const value of values) {
      const result = numeric(value);
      if (result !== null) {
        return result;
      }
    }
    return null;
  }

  function numeric(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function firstValue(...values) {
    return values.find((value) => value !== null && value !== undefined);
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function node(tagName, className = "", text = null) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (text !== null && text !== undefined) {
      element.textContent = String(text);
    }
    return element;
  }

  function clear(element) {
    element.replaceChildren();
  }
})();
