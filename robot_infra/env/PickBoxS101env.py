
from typing import Any, Dict, Literal, Tuple
from pathlib import Path
import mujoco
import numpy as np
from gymnasium import spaces

from mujoco_gym_env import S101GymEnv, GymRenderingSpec
from viewer_wrapper import PassiveViewerWrapper

_S101_HOME = np.zeros(6, dtype=np.float32) 
_CARTESIAN_BOUNDS = np.asarray([[0.1, -0.1, 0], [0.28, 0.28, 0.28]])
_SAMPLING_BOUNDS = np.asarray([[0.3, -0.15], [0.5, 0.15]])


class S101PickCubeGymEnv(S101GymEnv):
    """Environment for a Panda robot picking up a cube."""

    def __init__(
        self,
        xml_path = None,
        seed: int = 0,
        control_dt: float = 0.1,
        physics_dt: float = 0.002,
        render_spec: GymRenderingSpec = GymRenderingSpec(),  # noqa: B008
        render_mode: Literal["rgb_array", "human"] = "rgb_array",
        image_obs: bool = False,
        reward_type: str = "sparse",
        random_block_position: bool = False,
    ):
        self.reward_type = reward_type

        super().__init__(
            xml_path=xml_path,
            seed=seed,
            control_dt=control_dt,
            physics_dt=physics_dt,
            render_spec=render_spec,
            render_mode=render_mode,
            image_obs=image_obs,
            home_position=_S101_HOME,
            cartesian_bounds=_CARTESIAN_BOUNDS,
        )

        # Task-specific setup
        self._block_z = self._model.geom("block").size[2]
        self._random_block_position = random_block_position

        # Setup observation space properly to match what _compute_observation returns
        # Observation space design:
        #   - "state":  agent (robot) configuration as a single Box
        #   - "environment_state": block position in the world as a single Box
        #   - "pixels": (optional) dict of camera views if image observations are enabled

        agent_dim = self.get_robot_state().shape[0]
        agent_box = spaces.Box(-np.inf, np.inf, (agent_dim,), dtype=np.float32)
        env_box = spaces.Box(-np.inf, np.inf, (3,), dtype=np.float32)

        if self.image_obs:
            self.observation_space = spaces.Dict(
                {
                    "pixels": spaces.Dict(
                        {
                            "front": spaces.Box(
                                0,
                                255,
                                (self._render_specs.height, self._render_specs.width, 3),
                                dtype=np.uint8,
                            ),
                            "wrist": spaces.Box(
                                0,
                                255,
                                (self._render_specs.height, self._render_specs.width, 3),
                                dtype=np.uint8,
                            ),
                        }
                    ),
                    "agent_pos": agent_box,
                }
            )
        else:
            self.observation_space = spaces.Dict(
                {
                    "agent_pos": agent_box,
                    "environment_state": env_box,
                }
            )

    def reset(self, seed=None, **kwargs) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
        """Reset the environment."""
        # Ensure gymnasium internal RNG is initialized when a seed is provided
        super().reset(seed=seed)

        mujoco.mj_resetData(self._model, self._data)

        # Reset the robot to home position
        self.reset_robot()

        # Sample a new block position
        if self._random_block_position:
            block_xy = np.random.uniform(*_SAMPLING_BOUNDS)
            self._data.jnt("block").qpos[:3] = (*block_xy, self._block_z)
        else:
            block_xy = np.asarray([0.5, 0.0])
            self._data.jnt("block").qpos[:3] = (*block_xy, self._block_z)
        mujoco.mj_forward(self._model, self._data)

        # Cache the initial block height
        self._z_init = self._data.sensor("block_pos").data[2]
        self._z_success = self._z_init + 0.1

        obs = self._compute_observation()
        return obs, {}

    def step(self, action: np.ndarray) -> Tuple[Dict[str, np.ndarray], float, bool, bool, Dict[str, Any]]:
        """Take a step in the environment."""
        # Apply the action to the robot
        self.apply_action(action)

        # Compute observation, reward and termination
        obs = self._compute_observation()
        rew = self._compute_reward()
        success = self._is_success()

        if self.reward_type == "sparse":
            success = rew == 1.0

        # Check if block is outside bounds
        block_pos = self._data.sensor("block_pos").data
        exceeded_bounds = np.any(block_pos[:2] < (_SAMPLING_BOUNDS[0] - 0.05)) or np.any(
            block_pos[:2] > (_SAMPLING_BOUNDS[1] + 0.05)
        )

        terminated = bool(success or exceeded_bounds)

        return obs, rew, terminated, False, {"succeed": success}

    def _compute_observation(self) -> dict:
        """Compute the current observation."""
        # Create the dictionary structure that matches our observation space
        observation = {}

        # Get robot state
        robot_state = self.get_robot_state().astype(np.float32)

        # Assemble observation respecting the newly defined observation_space
        block_pos = self._data.sensor("block_pos").data.astype(np.float32)

        if self.image_obs:
            # Image observations
            front_view, wrist_view = self.render()
            observation = {
                "pixels": {"front": front_view, "wrist": wrist_view},
                "agent_pos": robot_state,
            }
        else:
            # State-only observations
            observation = {
                "agent_pos": robot_state,
                "environment_state": block_pos,
            }

        return observation

    def _compute_reward(self) -> float:
        """Compute reward based on current state."""
        block_pos = self._data.sensor("block_pos").data

        if self.reward_type == "dense":
            tcp_pos = self._data.sensor("pinch_pos").data
            dist = np.linalg.norm(block_pos - tcp_pos)
            r_close = np.exp(-20 * dist)
            r_lift = (block_pos[2] - self._z_init) / (self._z_success - self._z_init)
            r_lift = np.clip(r_lift, 0.0, 1.0)
            return 0.3 * r_close + 0.7 * r_lift
        else:
            lift = block_pos[2] - self._z_init
            return float(lift > 0.1)

    def _is_success(self) -> bool:
        """Check if the task is successfully completed."""
        block_pos = self._data.sensor("block_pos").data
        tcp_pos = self._data.sensor("pinch_pos").data
        dist = np.linalg.norm(block_pos - tcp_pos)
        lift = block_pos[2] - self._z_init
        return dist < 0.05 and lift > 0.1

if __name__ == "__main__":
    import os
    import sys

    # Add the parent directory to the Python path
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Example usage
    env = S101PickCubeGymEnv(xml_path=Path("low_cost_robot_arm/scene_box.xml"))
    env = PassiveViewerWrapper(env)
    env.reset()
    action = np.zeros(7)  # Example action
    for _ in range(5000):
        env.step(np.random.uniform(-1, 1, 7))
    env.close()

