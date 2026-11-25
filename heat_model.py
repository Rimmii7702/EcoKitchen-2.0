from pathlib import Path

import numpy as np
import pandas as pd

from runtime_config import GeminiModel, InferenceError, optional_import, parse_gemini_json


def load_image(image_path, cv2=None):
    cv2 = cv2 or optional_import("cv2")
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError("Could not read the uploaded kitchen image.")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def analyze_with_gemini(kitchen_image, gemini_model, image_module=None):
    image_module = image_module or optional_import("PIL.Image")

    prompt = """
    Analyze this kitchen image and identify:
    1. All visible kitchen stations or areas (e.g., prep station, grill, sink, storage)
    2. For each area, estimate potential waste generation level (High, Medium, Low)
    3. Provide approximate x,y coordinates for each area in the image (as percentage of width/height)

    Format your response as JSON only:
    {
      "areas": [
        {
          "name": "station name",
          "waste_level": "High/Medium/Low",
          "x_percent": 0.XX,
          "y_percent": 0.XX
        }
      ]
    }
    """

    response = gemini_model.generate_content([prompt, image_module.fromarray(kitchen_image)])
    areas = parse_gemini_json(response.text).get("areas")
    if not isinstance(areas, list) or not areas:
        raise InferenceError("Gemini did not identify any kitchen stations.")

    height, width = kitchen_image.shape[:2]
    waste_map = {"Low": 1.5, "Medium": 5.0, "High": 8.5}
    waste_data_list = []
    try:
        for area in areas:
            name = area["name"]
            level = area["waste_level"]
            x = float(area["x_percent"])
            y = float(area["y_percent"])
            if not isinstance(name, str) or not name.strip() or not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("Invalid station data")
            waste_data_list.append({
                "area": name,
                "waste_level": level,
                "waste_kg": waste_map[level],
                "x": min(int(x * width), width - 1),
                "y": min(int(y * height), height - 1),
            })
    except (KeyError, TypeError, ValueError) as exc:
        raise InferenceError("Gemini returned invalid kitchen station data.") from exc
    return pd.DataFrame(waste_data_list)


def generate_heatmap(kitchen_image, waste_data, output_path, alpha=0.6, radius=100, cv2=None):
    cv2 = cv2 or optional_import("cv2")
    h, w = kitchen_image.shape[:2]
    heat = np.zeros((h, w), dtype=np.float32)
    max_w = waste_data['waste_kg'].max()

    for _, row in waste_data.iterrows():
        x, y = int(row["x"]), int(row["y"])
        intensity = row["waste_kg"] / max_w
        for i in range(max(0, y-radius), min(h, y+radius)):
            for j in range(max(0, x-radius), min(w, x+radius)):
                dist = np.sqrt((i-y)**2 + (j-x)**2)
                if dist < radius:
                    heat[i, j] += intensity * np.exp(-(dist**2)/(2*(radius/3)**2))

    heat /= np.max(heat)
    heat_img = (heat * 255).astype(np.uint8)
    color_map = cv2.applyColorMap(heat_img, cv2.COLORMAP_JET)
    color_map = cv2.cvtColor(color_map, cv2.COLOR_BGR2RGB)
    heatmap = cv2.addWeighted(kitchen_image, 1 - alpha, color_map, alpha, 0)

    for _, row in waste_data.iterrows():
        cv2.circle(heatmap, (int(row["x"]), int(row["y"])), 5, (0, 0, 0), -1)
        cv2.putText(heatmap, f"{row['area']}: {row['waste_kg']:.1f}kg",
                    (int(row["x"]) + 10, int(row["y"])), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)

    cv2.putText(heatmap, "Simulated kg by waste level", (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 2)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), cv2.cvtColor(heatmap, cv2.COLOR_RGB2BGR)):
        raise InferenceError("Could not save the kitchen heatmap.")
    return output_path

def generate_distribution_graph(waste_data, output_path, cv2=None):
    cv2 = cv2 or optional_import("cv2")

    waste_dist = waste_data.groupby('area')['waste_kg'].sum()
    total_waste = waste_dist.sum()
    areas = waste_dist.index
    weights = waste_dist.values

    img_height = 400
    img_width = 600
    bar_width = img_width // (len(areas) * 2)
    max_height = 300
    padding = 50

    graph_img = np.ones((img_height, img_width, 3), dtype=np.uint8) * 255

    for i, (area, weight) in enumerate(zip(areas, weights)):
        bar_height = int((weight / weights.max()) * max_height)
        x_start = i * bar_width * 2 + padding
        y_start = img_height - padding - bar_height
        cv2.rectangle(graph_img, (x_start, y_start), (x_start + bar_width, img_height - padding),
                      (0, 0, 255), -1)
        cv2.putText(graph_img, f"{area}: {(weight/total_waste*100):.1f}%", (x_start, y_start - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)

    cv2.line(graph_img, (padding, img_height - padding), (img_width - padding, img_height - padding), (0, 0, 0), 2)
    cv2.line(graph_img, (padding, padding), (padding, img_height - padding), (0, 0, 0), 2)
    cv2.putText(graph_img, "Simulated Waste Distribution by Area", (padding, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), graph_img):
        raise InferenceError("Could not save the waste distribution graph.")
    return output_path

def get_distribution_text(waste_data):
    if waste_data is None:
        return "No waste data available."
    waste_dist = waste_data.groupby('area')['waste_kg'].sum()
    total_waste = waste_dist.sum()
    dist_text = "Waste Distribution by Area (%):\n"
    for area, kg in waste_dist.items():
        percent = (kg / total_waste) * 100
        dist_text += f"{area}: {percent:.1f}% ({kg:.1f}kg)\n"
    return dist_text

def process_kitchen_image(image_path, heatmap_output, dist_graph_output, gemini_model=None, cv2=None, image_module=None):
    gemini_model = gemini_model or GeminiModel()
    cv2 = cv2 or optional_import("cv2")
    kitchen_image = load_image(image_path, cv2)
    waste_data = analyze_with_gemini(kitchen_image, gemini_model, image_module)
    heatmap_path = generate_heatmap(kitchen_image, waste_data, heatmap_output, cv2=cv2)
    dist_graph_path = generate_distribution_graph(waste_data, dist_graph_output, cv2=cv2)
    dist_text = get_distribution_text(waste_data)
    total_waste = waste_data["waste_kg"].sum()
    return {
        "waste_data": waste_data.to_dict(orient="records"),
        "heatmap_path": heatmap_path,
        "dist_graph_path": dist_graph_path,
        "dist_text": dist_text,
        "total_waste": total_waste
    }
