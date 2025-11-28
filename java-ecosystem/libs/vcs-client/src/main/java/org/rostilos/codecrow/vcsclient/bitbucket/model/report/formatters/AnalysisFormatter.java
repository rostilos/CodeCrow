package org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters;


import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

public interface AnalysisFormatter {
    String format(AnalysisSummary summary);
}
