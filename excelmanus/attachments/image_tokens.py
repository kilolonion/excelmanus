"""DeepSeek v41 vision-token calculator, ported verbatim."""

from __future__ import annotations

from dataclasses import dataclass

PATCH_SIZE = 14
DOWNSAMPLE_RATIO = 3
MAX_IMAGE_TOKENS = 1024
MIN_PIXELS = 544 * 544
CELL_SIZE = PATCH_SIZE * DOWNSAMPLE_RATIO


def _int_div(value: int, divisor: int) -> int:
    return value // divisor


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _grid_tokens(grid_height: int, grid_width: int) -> int:
    return grid_height * (grid_width + 1) + 2


def _grid_cells(padded_length: int) -> int:
    return _ceil_div(_int_div(padded_length, PATCH_SIZE), DOWNSAMPLE_RATIO)


@dataclass(frozen=True)
class _GridResize:
    grid_height: int
    grid_width: int
    best_height: int
    best_width: int
    num_tokens: int


def _solve_resize_ratio(height: int, width: int, budget: int) -> _GridResize:
    aspect = height / width
    ideal_grid_width = ((budget - 2) / aspect + 0.25) ** 0.5 - 0.5
    ideal_grid_height = ideal_grid_width * aspect
    if ideal_grid_width < 1:
        solved_grid_width = 1
        solved_grid_height = _int_div(budget - 2, solved_grid_width + 1)
        best_width = solved_grid_width * CELL_SIZE
        best_height = solved_grid_height * CELL_SIZE
    elif ideal_grid_height < 1:
        solved_grid_height = 1
        solved_grid_width = _int_div(budget - 2, solved_grid_height) - 1
        best_width = solved_grid_width * CELL_SIZE
        best_height = solved_grid_height * CELL_SIZE
    else:
        solved_grid_width = int(ideal_grid_width)
        solved_grid_height = int(ideal_grid_height)
        scale = min(
            solved_grid_width * CELL_SIZE / width,
            solved_grid_height * CELL_SIZE / height,
        )
        best_width = int(width * scale / PATCH_SIZE) * PATCH_SIZE
        best_height = int(height * scale / PATCH_SIZE) * PATCH_SIZE
    grid_height = _grid_cells(best_height)
    grid_width = _grid_cells(best_width)
    return _GridResize(grid_height, grid_width, best_height, best_width, _grid_tokens(grid_height, grid_width))


def _safe_resize(height: int, width: int, padded_height: int, padded_width: int) -> _GridResize:
    grid_height = _grid_cells(padded_height)
    grid_width = _grid_cells(padded_width)
    direct = _GridResize(
        grid_height, grid_width, padded_height, padded_width, _grid_tokens(grid_height, grid_width),
    )
    if direct.num_tokens <= MAX_IMAGE_TOKENS:
        return direct
    solved = _solve_resize_ratio(height, width, MAX_IMAGE_TOKENS)
    if solved.num_tokens > MAX_IMAGE_TOKENS:
        raise ValueError(f"deepseek image tokens: no grid fits the token budget for {width}x{height}")
    return solved


def _resize_once(width: int, height: int) -> _GridResize:
    scaled_width, scaled_height = width, height
    pixels = scaled_width * scaled_height
    if 0 < pixels < MIN_PIXELS:
        scale = (MIN_PIXELS / pixels) ** 0.5
        scaled_width = int(scaled_width * scale)
        scaled_height = int(scaled_height * scale)
    padded_width = _ceil_div(scaled_width, PATCH_SIZE) * PATCH_SIZE
    padded_height = _ceil_div(scaled_height, PATCH_SIZE) * PATCH_SIZE
    return _safe_resize(scaled_height, scaled_width, padded_height, padded_width)


def deepseek_image_tokens(width: int, height: int) -> int:
    """Vision tokens DeepSeek charges for one request image. At most 1024."""
    result = _resize_once(width, height)
    for _ in range(1, 10):
        nxt = _resize_once(result.best_width, result.best_height)
        if nxt == result:
            return result.num_tokens
        result = nxt
    raise ValueError(f"deepseek image tokens: resize did not converge for {width}x{height}")


def estimate_image_tokens(width: int, height: int, *, deepseek: bool = False) -> int:
    if width <= 0 or height <= 0:
        return 85
    if deepseek:
        return deepseek_image_tokens(width, height)
    return min(4096, max(85, (width * height) // 750))
