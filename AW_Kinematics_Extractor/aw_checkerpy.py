import numpy as np
import json, maude

def load_data(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

def find_closest_entry(desired_timestamp, start_id, data, key, time_step=0.1):
    i = start_id
    gap = time_step / 2
    closest_entry_id = None
    while i < len(data[key]):
        entry = data[key][i]
        time_diff = abs(entry['timestamp'] - desired_timestamp)
        if entry['timestamp'] > desired_timestamp and time_diff > gap:
            break
        if time_diff <= gap:
            gap = time_diff
            closest_entry_id = i
        i += 1
    return closest_entry_id


def encode_vector3(v):
    return f'{v["x"]} {v["y"]} {v["z"]}'

def normalize_angle_degrees_0_360(angle_degrees):
    normalized_angle = angle_degrees % 360
    if normalized_angle < 0:
        normalized_angle += 360
    return normalized_angle

def encode_euler_angles(angle):
    def format(angle):
        return normalize_angle_degrees_0_360(angle)
    return f'{format(angle["x"])} {format(angle["y"])} {format(angle["z"])}'

def encode_pose(pose):
    str = f'posi: {encode_vector3(pose["position"])}, rota: {encode_euler_angles(pose["rotation"])}'
    return "{" + str + "}"

def encode_twist(twist):
    str = f'lin: {encode_vector3(twist["linear"])}, ang: {encode_vector3(twist["angular"])}'
    return "{" + str + "}"

def encode_classification(classification):
    str_classes = [f"{entry['label']} -> {entry['probability']}" for entry in classification]
    return ", ".join(str_classes)

def encode_shape(shape):
    if shape['type'] == 'box':
        dims = shape['size']
        return "box-shape(" + f"{dims['x']} {dims['y']} {dims['z']}" + ")"
    if shape['type'] == 'polygon':
        points = shape['footprint']
        str_points = ["(" + encode_vector3(pt) + ")" for pt in points]
        return "polygon-shape(" + " ".join(str_points) + ")"
    raise ValueError(f"Unknown shape type: {shape['type']}")

def encode_perception_object(perception_obj):
    id = perception_obj['id']
    str = (f'id: "{id}", '
           f"epro: {perception_obj['existence_prob']}, "
           f"class: [{encode_classification(perception_obj['classification'])}], "
           f"pose: {encode_pose(perception_obj['pose'])}, "
           f"twist: {encode_twist(perception_obj['twist'])}, "
           f"accel: {encode_twist(perception_obj['acceleration'])}, "
           f"shape: {encode_shape(perception_obj['shape'])}")
    return "{" + str + "}"

def encode_gt_ego(ego_kin):
    str = (f"pose: {encode_pose(ego_kin['pose'])}, "
           f"twist: {encode_twist(ego_kin['twist'])}, "
           f"accel: {encode_twist(ego_kin['acceleration'])}")
    return "{" + str + "}"

def encode_gt_npc_vehicle(npc_kin):
    name = npc_kin["name"]
    bbox = npc_kin["bounding_box"]
    str = (f'name: "{name}", '
           f"pose: {encode_pose(npc_kin['pose'])}, "
           f"twist: {encode_twist(npc_kin['twist'])}, "
           f"accel: {npc_kin['accel']}, "
           f"gt-rect: {bbox['x']} {bbox['y']} {bbox['width']} {bbox['height']}")
    return "{" + str + "}"

def encode_bbox_perception_object(bbox_obj):
    rect = bbox_obj['bounding_box']
    min_x = float(rect['x'])
    max_y = float(rect['y'])
    width = float(rect['width'])
    height = float(rect['height'])
    # In sensor_msgs/RegionOfInterest, y is the starting row (from top), so max_y - height gives the min_y (from bottom)
    str = (f"epro: {bbox_obj['existence_prob']}, "
           f"class: [{encode_classification(bbox_obj['classification'])}], "
           f"bb-rect: {min_x} {max_y-height} {width} {height}")
    return "{" + str + "}"

def encode_state(timestamp, perp_entry, gt_kinematic, bbox_entry=None):
    # build the groundtruth kinematics encoding
    ego_kin = gt_kinematic['groundtruth_ego']
    ego_kin_encoding = encode_gt_ego(ego_kin)

    npcs_kin = gt_kinematic['groundtruth_vehicles']
    if len(npcs_kin) == 0:
        npcs_encoding = "empty"
    else:
        npc_strs = [encode_gt_npc_vehicle(npc_kin) for npc_kin in npcs_kin]
        npcs_encoding = ", ".join(npc_strs)
        if len(npc_strs) > 1:
            npcs_encoding = "(" + npcs_encoding + ")"

    # build the perception objects encoding
    if perp_entry is None or len(perp_entry['objects']) == 0:
        perp_objs_encoding = "empty"
    else:
        perp_obj_strs = [encode_perception_object(perception_obj) for perception_obj in perp_entry['objects']]
        perp_objs_encoding = ", ".join(perp_obj_strs)
        if len(perp_obj_strs) > 1:
            perp_objs_encoding = "(" + perp_objs_encoding + ")"

    # build the bounding box perception objects encoding
    if bbox_entry is None or len(bbox_entry['objects']) == 0:
        bbox_perp_objs_encoding = "empty"
    else:
        bbox_perp_obj_strs = [encode_bbox_perception_object(perception_obj) for perception_obj in bbox_entry['objects']]
        bbox_perp_objs_encoding = ", ".join(bbox_perp_obj_strs)
        if len(bbox_perp_obj_strs) > 1:
            bbox_perp_objs_encoding = "(" + bbox_perp_objs_encoding + ")"

    state = f'{timestamp} | {ego_kin_encoding} | {npcs_encoding} | {perp_objs_encoding} | {bbox_perp_objs_encoding}'
    return state

def process_a_file(file_path, formulas):
    import_model()

    data = load_data(file_path)
    is_bbox_available = 'boundingbox_perception_objects' in data and \
                        len(data['boundingbox_perception_objects']) > 0
    maude_mod = maude.getModule('PROPOSITIONS')
    prev_state = None
    time_step = 0.1
    input_text = \
"""
mod TRANSITIONS is 
pr PROPOSITIONS .

"""
    input_text += f"eq time-step = {int(time_step * 1000)} .\n"

    # parse the vehicle dimensions
    size_map_strs = []
    center_offset_map_strs = []
    for entry in data['groundtruth_size']['vehicle_sizes']:
        name = entry['name']
        dims = entry['size']
        centers = entry['center']
        size_map_strs.append(f'"{name}" |-> {dims["x"]} {dims["y"]} {dims["z"]}')
        center_offset_map_strs.append(f'"{name}" |-> {centers["x"]} {centers["y"]} {centers["z"]}')

    input_text += f"eq gtSizes = {', '.join(size_map_strs)} .\n"
    input_text += f"eq gtCenters = {', '.join(center_offset_map_strs)} .\n"
    # print(input_text)

    # parse the states and transitions
    start_time = round(data['groundtruth_kinematic'][0]['timestamp'] + 0.05, 1)
    end_time = round(data['groundtruth_kinematic'][-1]['timestamp'] - 0.05, 1)
    kin_id = 0
    perp_id = 0
    bbox_id = 0
    timestamp = start_time
    while timestamp <= end_time:
        # extract the groundtruth kinematics entry
        closest_kin_entry_id = find_closest_entry(timestamp, kin_id, data, 'groundtruth_kinematic', time_step=time_step)
        if closest_kin_entry_id is None:
            print(f"No close groundtruth kinematics found for timestamp {timestamp}. Stop.")
            break
        gt_kinematic = data['groundtruth_kinematic'][closest_kin_entry_id]
        kin_id = closest_kin_entry_id + 1

        # extract the perception objects entry
        perp_obj_entry = None
        closest_perp_entry_id = find_closest_entry(timestamp, perp_id, data, 'perception_objects', time_step=time_step)
        if closest_perp_entry_id is not None:
            perp_obj_entry = data['perception_objects'][closest_perp_entry_id]
            perp_id = closest_perp_entry_id + 1

        # extract the bounding box perception objects entry
        bbox_entry = None
        if is_bbox_available:
            closest_bbox_entry_id = find_closest_entry(timestamp, bbox_id, data, 'boundingbox_perception_objects', time_step=time_step)
            if closest_bbox_entry_id is not None:
                bbox_entry = data['boundingbox_perception_objects'][closest_bbox_entry_id]
                bbox_id = closest_bbox_entry_id + 1
    
        # encode the state
        state = encode_state(timestamp, perp_obj_entry, gt_kinematic, bbox_entry)
        if prev_state is None:
            input_text += f"eq init = {state} .\n"
        else:
            input_text += f"rl {prev_state} => {state} .\n"
        prev_state = state

        timestamp += time_step

    input_text += f'rl {prev_state} => {prev_state} .\n'
    input_text += "endm\n"

    maude.input(input_text)
    print("Parsed and loaded the state machine.")
    m = maude.getModule('TRANSITIONS')

    initT = m.parseTerm("init")
    rewrite_grap = maude.RewriteGraph(initT)
    for formula in formulas:
        print(f"Checking formula: {formula}")
        re = rewrite_grap.modelCheck(m.parseTerm(formula))
        print(f"Model checking result: {re.holds}")
        if not re.holds:
            print(f"Counterexample path:")
            print(re.leadIn)

def import_model():
    maude.init()
    maude.load('formal-model/props.maude')

def make_cli():
    import argparse

    parser = argparse.ArgumentParser(description="AW-CheckerPy: LTL-based property checker")
    parser.add_argument("path", help="Path to a JSON trace file")
    parser.add_argument("formulas", help="LTL formulas to check. If not provided, will load from formulas.txt", nargs='*')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = make_cli()
    if len(args.formulas) == 0:
        print(f"No formulas provided. Loading from formulas.txt")
        with open("formulas.txt", "r") as file:
            for line in file:
                args.formulas.append(line.strip())
    process_a_file(args.path, args.formulas)