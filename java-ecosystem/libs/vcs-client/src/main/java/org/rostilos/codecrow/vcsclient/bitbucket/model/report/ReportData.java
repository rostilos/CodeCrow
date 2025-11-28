package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;

public class ReportData {
    private final String title;
    private final DataValue value;
    @JsonProperty("type")
    private final String type;

    @JsonCreator
    public ReportData(@JsonProperty("title") String title, @JsonProperty("value") DataValue value) {
        this.title = title;
        this.value = value;
        this.type = typeFrom(value);
    }

    private static String typeFrom(DataValue value) {
        if (value instanceof DataValue.Link || value instanceof DataValue.CloudLink) {
            return "LINK";
        } else {
            return "TEXT";
        }
    }

    public String getTitle() {
        return title;
    }

    public DataValue getValue() {
        return value;
    }

    public String getType() {
        return type;
    }
}

