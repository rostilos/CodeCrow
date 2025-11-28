package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonValue;

import java.io.Serializable;

public interface DataValue extends Serializable {

    record Link(String linktext, String href) implements DataValue {
            @JsonCreator
            public Link(@JsonProperty("linktext") String linktext, @JsonProperty("href") String href) {
                this.linktext = linktext;
                this.href = href;
            }
        }

    record CloudLink(String text, String href) implements DataValue {
            @JsonCreator
            public CloudLink(@JsonProperty("text") String text, @JsonProperty("href") String href) {
                this.text = text;
                this.href = href;
            }
        }

    record Text(String value) implements DataValue {
            @JsonCreator
            public Text(@JsonProperty("value") String value) {
                this.value = value;
            }

            @Override
            @JsonValue
            public String value() {
                return value;
            }
        }
}
