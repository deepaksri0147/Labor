import argparse
import ruamel.yaml
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as dq, SingleQuotedScalarString as sq, LiteralScalarString as ls
def format_value(value):
    if value.startswith('{') and value.endswith('}'):
        return ls(value)
    elif value.startswith('[') and value.endswith(']'):
        return ls(value)
    elif value.startswith('"') and value.endswith('"'):
        return dq(value.strip('"'))
    elif value.startswith("'") and value.endswith("'"):
        return sq(value.strip("'"))
    elif value == "":
        return ruamel.yaml.scalarstring.PlainScalarString(value)
    else:
        return dq(value)
def parse_properties(input_file):
    properties = {}
    current_key = None
    current_value_lines = []
    inside_multiline = False
    with open(input_file, 'r') as file:
        for line in file:
            line = line.rstrip()
            if not line or line.startswith('#'):
                continue
            if not inside_multiline and ('=' in line or ':' in line):
                if current_key is not None:
                    properties[current_key] = '\n'.join(current_value_lines).replace(',/', ',').replace('\\', '')
                if '=' in line:
                    current_key, current_value = line.split('=', 1)
                else:
                    current_key, current_value = line.split(':', 1)
                current_key = current_key.strip()
                current_value_lines = [current_value.strip()]
                if current_value.strip().endswith(('\\', '[', '{')):
                    inside_multiline = True
            elif inside_multiline:
                current_value_lines.append(line)
                if line.endswith((']', '}')) and not line.endswith(('\\', ',/')):
                    inside_multiline = False
            else:
                current_value_lines.append(line.strip())
        if current_key is not None:
            properties[current_key] = '\n'.join(current_value_lines).replace(',/', ',').replace('\\', '')
    return properties
def update_configmap(input_file, existing_yaml, output_yaml, remove_unmatched=False):
    properties = parse_properties(input_file)
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with open(existing_yaml, 'r') as file:
        data = yaml.load(file)
    if 'configmap' not in data:
        data['configmap'] = {}
    updated = False
    remove_keys = set(data['configmap'].keys()) - set(properties.keys())
    for key, value in properties.items():
        formatted_value = format_value(value)
        if key not in data['configmap'] or data['configmap'][key] != formatted_value:
            data['configmap'][key] = formatted_value
            updated = True
    if remove_unmatched:
        for key in remove_keys:
            del data['configmap'][key]
            updated = True
    if updated:
        with open(output_yaml, 'w') as file:
            yaml.dump(data, file)
def main():
    parser = argparse.ArgumentParser(description='Update values.yaml based on properties file')
    parser.add_argument('input_file', help='Path to the properties file to read')
    args = parser.parse_args()
    existing_yaml = 'values.yaml'
    output_yaml = 'values.yaml'  # Writing back to the same file
    remove_unmatched = True  # This can be changed as needed
    update_configmap(args.input_file, existing_yaml, output_yaml, remove_unmatched)
if __name__ == "__main__":
    main()
