import json
import numpy as np
import re

def generate_parametric_mesh(shape_type, sx, sy, sz):
    """Generates dynamic point clouds for the Plotly Alpha-Hull wrapper."""
    shape_type = str(shape_type).lower()
    
    if shape_type == "sphere":
        # Math for a 3D Sphere (stretches based on sx, sy, sz)
        u = np.linspace(0, 2 * np.pi, 30)
        v = np.linspace(0, np.pi, 30)
        x = sx * np.outer(np.cos(u), np.sin(v)).flatten()
        y = sy * np.outer(np.sin(u), np.sin(v)).flatten()
        z = sz * np.outer(np.ones(np.size(u)), np.cos(v)).flatten()
        return {"x": x.tolist(), "y": y.tolist(), "z": z.tolist(), "is_cloud": True}
        
    elif shape_type == "cylinder":
        # Math for a 3D Cylinder
        z_points = np.linspace(-sz, sz, 20)
        theta = np.linspace(0, 2*np.pi, 30)
        theta_grid, z_grid = np.meshgrid(theta, z_points)
        x = sx * np.cos(theta_grid).flatten()
        y = sy * np.sin(theta_grid).flatten()
        z = z_grid.flatten()
        return {"x": x.tolist(), "y": y.tolist(), "z": z.tolist(), "is_cloud": True}
        
    elif shape_type == "cone" or shape_type == "pyramid":
        # Math for a Cone (or Pyramid if resolution is low)
        res = 4 if shape_type == "pyramid" else 30
        theta = np.linspace(0, 2*np.pi, res, endpoint=False)
        x = np.append(sx * np.cos(theta), 0)
        y = np.append(sy * np.sin(theta), 0)
        z = np.append(-sz * np.ones_like(theta), sz)
        return {"x": x.tolist(), "y": y.tolist(), "z": z.tolist(), "is_cloud": True}
        
    else:
        # Default to a precise Box (using strict geometry)
        x = [-sx, sx, sx, -sx, -sx, sx, sx, -sx]
        y = [-sy, -sy, sy, sy, -sy, -sy, sy, sy]
        z = [-sz, -sz, -sz, -sz, sz, sz, sz, sz]
        i = [7, 0, 0, 0, 4, 4, 6, 6, 4, 0, 3, 2]
        j = [3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 3]
        k = [0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6]
        return {"x": x, "y": y, "z": z, "i": i, "j": j, "k": k, "is_cloud": False}

def process_graph_data(g_data):
    """Sanitizes data and routes it to the correct mathematical engine."""
    print(f"[📊 MATH ENGINE]: Preparing {g_data.get('type', 'bar').upper()} structure...")

    graph_type = g_data.get("type", "bar").lower()

    # ⚡ SPRINT 18: PARAMETRIC MESH ROUTER ⚡
    if graph_type == "model":
        m_type = g_data.get("model_type", "box")
        scale = g_data.get("scale", [1.0, 1.0, 1.0])
        try:
            sx, sy, sz = float(scale[0]), float(scale[1]), float(scale[2])
        except:
            sx, sy, sz = 1.0, 1.0, 1.0
            
        g_data["mesh"] = generate_parametric_mesh(m_type, sx, sy, sz)
        return json.dumps(g_data)

    # ... (Data sanitization for bar, line, and standard 3D graphs)
    labels = g_data.get("labels", [])
    raw_datasets = g_data.get("datasets", [])
    if not raw_datasets and "values" in g_data:
        raw_datasets = [{"name": "Data", "values": g_data["values"]}]

    clean_datasets = []
    for ds in raw_datasets:
        clean_values = []
        for v in ds.get("values", []):
            try:
                match = re.search(r"[-+]?\d*\.\d+|\d+", str(v).replace(',', ''))
                clean_values.append(float(match.group()) if match else 0.0)
            except:
                clean_values.append(0.0)
        clean_datasets.append({"name": ds.get("name", "Data"), "values": clean_values})

    g_data['datasets'] = clean_datasets

    if graph_type in ['bar', 'line', 'pie']:
        return json.dumps(g_data)

    x = np.linspace(-5, 5, 50)
    y = np.linspace(-5, 5, 50)
    X, Y = np.meshgrid(x, y)
    Z = np.zeros_like(X)

    first_ds_values = clean_datasets[0]["values"] if clean_datasets else []
    num_points = len(first_ds_values)
    
    if num_points == 0: return json.dumps(g_data)

    max_val = max(first_ds_values) if max(first_ds_values) > 0 else 1
    norm_vals = [(v / max_val) * 2.0 for v in first_ds_values]
    centers = np.linspace(-3, 3, num_points)

    annotations = []
    for i in range(num_points):
        peak = norm_vals[i] * np.exp(-((X - centers[i])**2 + (Y - 0)**2) / 2.0)
        Z += peak
        label_text = f"{labels[i]}<br>({first_ds_values[i]})" if i < len(labels) else str(first_ds_values[i])
        annotations.append({"x": centers[i], "y": 0, "z": norm_vals[i] + 0.2, "text": label_text})

    g_data['surface'] = {"x": X.tolist(), "y": Y.tolist(), "z": Z.tolist()}
    g_data['annotations'] = annotations
    return json.dumps(g_data)