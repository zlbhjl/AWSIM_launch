# ==========================================
# uturn_config.py (参考コード反映版)
# ==========================================

SCENARIO_TYPE = "uturn"
REPEAT_COUNT = 3000

PARAM_RANGES = {
    # 参考コードが 26.0~36.0 
    "dx0": (10.0, 25.0),         
    "ego_speed": (30.0, 40.0),   
    "npc_speed": (10.0, 25.0),   
}

# uturn_config.py の一部を変更
FIXED_PARAMS = {
    "ego_init_lane": "514", 
    "ego_init_offset": 30,  
    "ego_goal_lane": "516",
    "ego_goal_offset": 20,

    "npc_init_lane": "521",
    "npc_init_offset": 32,
    # ★ここを自車と同じ 514 に変更！（衝突させるため）
    "uturn_next_lane": "514", 
    
    "acceleration": 7.0
}
