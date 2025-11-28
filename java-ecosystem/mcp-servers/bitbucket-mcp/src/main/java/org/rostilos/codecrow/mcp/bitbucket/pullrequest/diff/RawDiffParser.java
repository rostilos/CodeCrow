package org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

public class RawDiffParser {
    public List<FileDiff> execute(String rawDiff) {
        List<FileDiff> fileDiffs = new ArrayList<>();
        String[] lines = rawDiff.split("\n");
        FileDiff currentFile = null;
        StringBuilder patchBuilder = new StringBuilder();
        for (String line : lines) {
            if(line.startsWith("index ")){
                continue;
            }
            if (line.startsWith("diff --git") ) {
                // Save previous diff
                if (currentFile != null) {
                    currentFile.setChanges(patchBuilder.toString());
                    fileDiffs.add(currentFile);
                }

                // Start new file
                patchBuilder.setLength(0);
                currentFile = new FileDiff();
                String[] parts = line.split(" ");
                currentFile.setFilePath(parts[2].substring(2));
            } else if(currentFile != null && currentFile.getDiffType() == null){
                Optional<FileDiff.DiffType> optionalDiffType = getDiffTypeFromFileLine(line);
                if (optionalDiffType.isPresent()) {
                    currentFile.setDiffType(optionalDiffType.get());
                }
            }

            if (currentFile != null) {
                patchBuilder.append(line).append("\n");
            }
        }
        if (currentFile != null) {
            currentFile.setChanges(patchBuilder.toString());
            fileDiffs.add(currentFile);
        }
        return fileDiffs;
    }

    //TODO: add renamed type, add modified handle
    private Optional<FileDiff.DiffType> getDiffTypeFromFileLine(String line) {
        if (line.startsWith("new file mode")) {
            return Optional.of(FileDiff.DiffType.ADDED);
        } else if (line.startsWith("deleted file mode")) {
            return Optional.of(FileDiff.DiffType.REMOVED);
        } else if (line.startsWith("--- ") && line.contains("/dev/null")) {
            return Optional.of(FileDiff.DiffType.ADDED);
        } else if (line.startsWith("--- ") && !line.contains("/dev/null")) {
            return Optional.of(FileDiff.DiffType.MODIFIED);
        }
        return Optional.empty();
    }
}
