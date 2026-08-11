"""
UI Configuration V2 for Dashboard-Style Analytics
Maps visualization specs to Angular Material components
"""

from typing import Dict, List, Any


class DashboardUIConfig:
    """Generate UI configuration for dashboard-style analytics"""

    @staticmethod
    def generate(dashboard_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate complete UI configuration from dashboard analytics.
        Similar to Zoho Analytics layout structure.
        """

        return {
            "layout": "dashboard",  # Full dashboard layout
            "header": DashboardUIConfig._create_header(dashboard_data["dashboard"]),
            "insights_bar": DashboardUIConfig._create_insights_bar(dashboard_data["insights"]),
            "visualizations": DashboardUIConfig._map_visualizations(dashboard_data["visualizations"]),
            "footer": DashboardUIConfig._create_footer(dashboard_data.get("data_quality", {})),
            "theme": {
                "primary": "#4A90E2",
                "background": "#1E1E1E",
                "text": "#FFFFFF",
                "grid": "#2A2A2A",
                "accent": "#6C5CE7"
            },
            "grid_layout": DashboardUIConfig._calculate_grid_layout(len(dashboard_data["visualizations"]))
        }

    @staticmethod
    def _create_header(dashboard_meta: Dict[str, Any]) -> Dict[str, Any]:
        """Create dashboard header configuration"""

        return {
            "component": "dashboard-header",
            "config": {
                "title": dashboard_meta.get("title", "Data Analytics"),
                "description": dashboard_meta.get("description", ""),
                "icon": "dashboard",
                "style": {
                    "background": "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
                    "padding": "32px",
                    "color": "#FFFFFF"
                }
            }
        }

    @staticmethod
    def _create_insights_bar(insights: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create insights summary bar (like KPI cards)"""

        return {
            "component": "insights-bar",
            "config": {
                "layout": "horizontal_scroll",
                "cards": [
                    {
                        "title": insight.get("title", ""),
                        "value": insight.get("value", ""),
                        "metric": insight.get("metric", ""),
                        "icon": insight.get("icon", "analytics"),
                        "color": DashboardUIConfig._get_insight_color(insight.get("type", ""))
                    }
                    for insight in insights
                ],
                "style": {
                    "background": "#2A2A2A",
                    "padding": "16px",
                    "margin": "16px 0"
                }
            }
        }

    @staticmethod
    def _map_visualizations(visualizations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Map each visualization to UI component configuration"""

        mapped = []

        for viz in visualizations:
            viz_type = viz.get("type")

            if viz_type == "horizontal_bar":
                mapped.append(DashboardUIConfig._create_horizontal_bar_config(viz))
            elif viz_type == "scatter":
                mapped.append(DashboardUIConfig._create_scatter_config(viz))
            elif viz_type == "grouped_bar":
                mapped.append(DashboardUIConfig._create_grouped_bar_config(viz))
            elif viz_type == "line":
                mapped.append(DashboardUIConfig._create_line_config(viz))
            elif viz_type == "pie":
                mapped.append(DashboardUIConfig._create_pie_config(viz))

        return mapped

    @staticmethod
    def _create_horizontal_bar_config(viz: Dict[str, Any]) -> Dict[str, Any]:
        """Configure horizontal bar chart (like 'Top Genres by Rating')"""

        return {
            "component": "horizontal-bar-chart",
            "id": "chart_" + viz["title"].lower().replace(" ", "_"),
            "grid_position": {"row": 0, "col": 0, "colspan": 6, "rowspan": 4},
            "config": {
                "title": viz.get("title", ""),
                "subtitle": viz.get("subtitle", ""),
                "data": viz.get("data", []),
                "xAxis": {
                    "label": viz.get("x_axis", "Value"),
                    "show": True,
                    "gridLines": True
                },
                "yAxis": {
                    "label": viz.get("y_axis", "Category"),
                    "show": True,
                    "width": "auto"
                },
                "style": {
                    "barColor": viz.get("config", {}).get("color", "#4A90E2"),
                    "barHeight": 24,
                    "showValues": viz.get("config", {}).get("show_values", True),
                    "valuePosition": "end",
                    "animation": {
                        "duration": 800,
                        "easing": "easeOutQuart"
                    }
                },
                "interaction": {
                    "hover": True,
                    "click": "drill_down"
                }
            }
        }

    @staticmethod
    def _create_scatter_config(viz: Dict[str, Any]) -> Dict[str, Any]:
        """Configure scatter plot"""

        return {
            "component": "scatter-chart",
            "id": "chart_" + viz["title"].lower().replace(" ", "_"),
            "grid_position": {"row": 0, "col": 6, "colspan": 6, "rowspan": 4},
            "config": {
                "title": viz.get("title", ""),
                "subtitle": viz.get("subtitle", ""),
                "insight": viz.get("insight", ""),
                "data": viz.get("data", []),
                "xAxis": {
                    "label": viz.get("x_axis", "X"),
                    "show": True,
                    "gridLines": True
                },
                "yAxis": {
                    "label": viz.get("y_axis", "Y"),
                    "show": True,
                    "gridLines": True
                },
                "style": {
                    "pointColor": viz.get("config", {}).get("point_color", "#4A90E2"),
                    "pointSize": viz.get("config", {}).get("point_size", 8),
                    "pointOpacity": 0.6,
                    "showTrendLine": viz.get("config", {}).get("show_trend_line", False),
                    "animation": {
                        "duration": 600,
                        "stagger": 10
                    }
                },
                "interaction": {
                    "hover": True,
                    "zoom": True,
                    "pan": True
                }
            }
        }

    @staticmethod
    def _create_grouped_bar_config(viz: Dict[str, Any]) -> Dict[str, Any]:
        """Configure grouped/stacked bar chart"""

        return {
            "component": "grouped-bar-chart",
            "id": "chart_" + viz["title"].lower().replace(" ", "_"),
            "grid_position": {"row": 4, "col": 0, "colspan": 6, "rowspan": 4},
            "config": {
                "title": viz.get("title", ""),
                "subtitle": viz.get("subtitle", ""),
                "data": viz.get("data", []),
                "xAxis": {
                    "label": viz.get("x_axis", "Category"),
                    "show": True,
                    "rotateLabels": 45
                },
                "yAxis": {
                    "label": viz.get("y_axis", "Count"),
                    "show": True,
                    "gridLines": True
                },
                "style": {
                    "barWidth": "auto",
                    "grouped": not viz.get("config", {}).get("stack", False),
                    "colorPalette": "category10",
                    "showLegend": viz.get("config", {}).get("show_legend", True),
                    "animation": {
                        "duration": 800,
                        "easing": "easeOutQuart"
                    }
                },
                "interaction": {
                    "hover": True,
                    "legendFilter": True
                }
            }
        }

    @staticmethod
    def _create_line_config(viz: Dict[str, Any]) -> Dict[str, Any]:
        """Configure line chart (temporal trends)"""

        return {
            "component": "line-chart",
            "id": "chart_" + viz["title"].lower().replace(" ", "_"),
            "grid_position": {"row": 4, "col": 6, "colspan": 6, "rowspan": 4},
            "config": {
                "title": viz.get("title", ""),
                "subtitle": viz.get("subtitle", ""),
                "data": viz.get("data", []),
                "xAxis": {
                    "label": viz.get("x_axis", "Time"),
                    "show": True,
                    "type": "linear"
                },
                "yAxis": {
                    "label": viz.get("y_axis", "Value"),
                    "show": True,
                    "gridLines": True
                },
                "style": {
                    "lineColor": viz.get("config", {}).get("line_color", "#4A90E2"),
                    "lineWidth": viz.get("config", {}).get("line_width", 3),
                    "showPoints": viz.get("config", {}).get("show_points", True),
                    "showArea": viz.get("config", {}).get("show_area", False),
                    "smooth": True,
                    "animation": {
                        "duration": 1000,
                        "easing": "easeInOutQuad"
                    }
                },
                "secondaryAxis": viz.get("config", {}).get("secondary_axis"),
                "interaction": {
                    "hover": True,
                    "zoom": True,
                    "crosshair": True
                }
            }
        }

    @staticmethod
    def _create_pie_config(viz: Dict[str, Any]) -> Dict[str, Any]:
        """Configure pie/donut chart"""

        return {
            "component": "pie-chart",
            "id": "chart_" + viz["title"].lower().replace(" ", "_"),
            "grid_position": {"row": 8, "col": 0, "colspan": 12, "rowspan": 4},
            "config": {
                "title": viz.get("title", ""),
                "subtitle": viz.get("subtitle", ""),
                "data": viz.get("data", []),
                "style": {
                    "type": "donut",  # or "pie"
                    "innerRadius": "50%",
                    "showPercentage": viz.get("config", {}).get("show_percentage", True),
                    "showLegend": viz.get("config", {}).get("show_legend", True),
                    "colorPalette": viz.get("config", {}).get("color_palette", "category20"),
                    "animation": {
                        "duration": 800,
                        "easing": "easeOutCubic"
                    }
                },
                "interaction": {
                    "hover": True,
                    "explode": True
                },
                "labels": {
                    "show": True,
                    "format": "percentage"
                }
            }
        }

    @staticmethod
    def _create_footer(data_quality: Dict[str, Any]) -> Dict[str, Any]:
        """Create dashboard footer with data quality info"""

        return {
            "component": "dashboard-footer",
            "config": {
                "powered_by": "UniDocVerse AI Analytics",
                "data_quality": {
                    "completeness": f"{data_quality.get('completeness', 0):.1f}%",
                    "records": data_quality.get('record_count', 0),
                    "fields": data_quality.get('field_count', 0)
                },
                "export_options": ["PDF", "Excel", "PNG"],
                "style": {
                    "background": "#2A2A2A",
                    "padding": "16px",
                    "text_align": "right"
                }
            }
        }

    @staticmethod
    def _calculate_grid_layout(num_visualizations: int) -> Dict[str, Any]:
        """Calculate responsive grid layout"""

        return {
            "type": "responsive_grid",
            "columns": 12,
            "row_height": "100px",
            "gap": "16px",
            "breakpoints": {
                "xl": {"columns": 12, "container_width": 1920},
                "lg": {"columns": 12, "container_width": 1440},
                "md": {"columns": 6, "container_width": 1024},
                "sm": {"columns": 6, "container_width": 768},
                "xs": {"columns": 12, "container_width": 480}
            }
        }

    @staticmethod
    def _get_insight_color(insight_type: str) -> str:
        """Get color for insight card based on type"""

        color_map = {
            "top_performer": "#00C851",
            "volume_leader": "#4A90E2",
            "time_span": "#FFA726",
            "overall_quality": "#AB47BC"
        }
        return color_map.get(insight_type, "#4A90E2")


def generate_ui_config_v2(dashboard_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate UI configuration from dashboard analytics.
    Returns complete configuration for Angular Material dashboard.
    """
    return DashboardUIConfig.generate(dashboard_data)