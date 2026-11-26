/**
 * macubuntu — shaderGlow.js
 * GPU Iridescent Glow + Animated Corner Radius for Celina Dynamic Island.
 *
 * Runs entirely on the GPU via Clutter.ShaderEffect — zero Cairo CPU rasterization,
 * no CSS style recalculations per frame. Animates corner radius smoothly without
 * reflows. Compatible with GNOME 46–51.
 */

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';

const FRAGMENT_SHADER = `
uniform sampler2D tex;
uniform vec2 u_size;
uniform float u_radius;
uniform float u_phase;
uniform float u_intensity;   // 0.0 = static idle, 1.0 = active AI glow
uniform vec3 u_state_tint;   // shifts hue by state (listening/thinking/speaking)

// Signed distance function for a smooth rounded rectangle (Dynamic Island pill)
float sdRoundedBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

// Cosine iridescent palette (Apple Intelligence 7-stop feel without array loops)
vec3 iridescence(float t) {
    vec3 a = vec3(0.55, 0.50, 0.62);
    vec3 b = vec3(0.45, 0.42, 0.48);
    vec3 c = vec3(1.00, 1.00, 1.00);
    vec3 d = vec3(0.00, 0.33, 0.67);
    return a + b * cos(6.28318 * (c * t + d));
}

void main() {
    vec2 uv = cogl_tex_coord_in[0].st;
    vec2 px = uv * u_size;
    vec2 half_size = u_size * 0.5;
    float r = min(u_radius, min(half_size.x, half_size.y));

    float dist = sdRoundedBox(px - half_size, half_size - vec2(1.5), r);
    // Crisp anti-aliased clip mask
    float mask = 1.0 - smoothstep(-0.75, 0.75, dist);

    // Base dark glass fill + child widget texture
    vec4 content = texture2D(tex, uv);
    vec4 glass = vec4(0.06, 0.06, 0.08, 0.88);
    vec3 base_rgb = mix(glass.rgb, content.rgb, content.a);
    float base_a = max(glass.a, content.a);

    // Rotating edge glow concentrated along the pill perimeter
    float angle = atan(px.y - half_size.y, px.x - half_size.x) / 6.28318;
    vec3 rainbow = iridescence(angle + u_phase);
    vec3 glow_color = mix(rainbow, u_state_tint, 0.30);

    // Inner-rim + hairline border luminance
    float rim = exp(-abs(dist) * 0.22) * u_intensity;
    float hairline = (1.0 - smoothstep(0.0, 1.6, abs(dist + 0.8))) * (0.25 + 0.75 * u_intensity);

    vec3 final_rgb = base_rgb + glow_color * (rim * 0.45 + hairline * 0.85);
    cogl_color_out = vec4(final_rgb * mask, base_a * mask);
}
`;

export const CelinaGlowEffect = GObject.registerClass(
class CelinaGlowEffect extends Clutter.ShaderEffect {
    _init() {
        super._init({ shader_type: Clutter.ShaderType.FRAGMENT_SHADER });
        this.set_shader_source(FRAGMENT_SHADER);
        this._phase = 0.0;
        this._radius = 21.0;
        this._intensity = 0.0;
        this._tint = [0.25, 0.65, 0.98];
    }

    setParams({ width, height, radius, phase, intensity, tint }) {
        if (width !== undefined && height !== undefined)
            this.set_uniform_value('u_size', [width, height]);
        if (radius !== undefined) {
            this._radius = radius;
            this.set_uniform_value('u_radius', radius);
        }
        if (phase !== undefined) {
            this._phase = phase;
            this.set_uniform_value('u_phase', phase);
        }
        if (intensity !== undefined) {
            this._intensity = intensity;
            this.set_uniform_value('u_intensity', intensity);
        }
        if (tint !== undefined) {
            this._tint = tint;
            this.set_uniform_value('u_state_tint', tint);
        }
    }
});
