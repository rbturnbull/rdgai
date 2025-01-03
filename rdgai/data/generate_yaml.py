import yaml

# Input and output file names
input_file = "language-subtag-registry"
output_file = "language-subtag-registry.yaml"

# Initialize variables
codes = {}

# Initialize variables
codes = {}
subtag = None
descriptions = []
current_key = None

# Read and parse the input file
with open(input_file, "r") as f:
    for line in f:
        line = line.rstrip()

        # Handle multi-line values (continuation lines start with a space)
        if line.startswith(" "):
            if current_key == "Description":
                descriptions[-1] += " " + line.strip()
            continue

        # Detect a new record
        if line == "%%":
            if subtag and descriptions:
                codes[subtag] = ", ".join(descriptions)
            subtag = None
            descriptions = []
            current_key = None
            continue

        # Extract key-value pairs
        if ": " in line:
            key, value = line.split(": ", 1)
            current_key = key.strip()

            if key == "Subtag":
                subtag = value.strip()
            elif key == "Description":
                descriptions.append(value.strip())

    # Add the last record if it exists
    if subtag and descriptions:
        codes[subtag] = ", ".join(descriptions)


# Write the YAML output
with open(output_file, "w") as f:
    f.write("# Derived from the IANA language subtag registry\n")
    f.write("# Source: https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry\n\n")
    yaml.dump(
        codes,
        f,
        sort_keys=False,  # Ensures dictionary order is maintained
        default_flow_style=False,  # Outputs YAML in a human-readable block format
    )

print(f"Converted to YAML format in {output_file}.")
