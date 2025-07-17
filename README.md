How to install the env:
`conda create -n mujoco_env python=3.10 -y`
`conda activate mujoco_env`
`pip install --upgrade pip`
`pip install mujoco`

To view the model use
`low_cost_robot_arm % python -m  mujoco.viewer --mjcf=scene_box.xml`

