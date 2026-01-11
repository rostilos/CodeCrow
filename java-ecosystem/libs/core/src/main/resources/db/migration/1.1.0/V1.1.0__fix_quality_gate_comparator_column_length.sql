-- Fix quality_gate_condition comparator column length
-- The original column was VARCHAR(10) but comparator values like 'GREATER_THAN_OR_EQUAL' need more space

ALTER TABLE quality_gate_condition 
ALTER COLUMN comparator TYPE VARCHAR(30);
