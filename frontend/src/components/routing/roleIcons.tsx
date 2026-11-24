import {
  BranchesOutlined,
  CustomerServiceOutlined,
  FlagOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import type { Role } from './types'

/** Un icono por tipo de nodo: la leyenda, el lienzo y los paneles usan los mismos. */
export const ROLE_ICON: Record<Role, React.ReactNode> = {
  entry: <PlayCircleOutlined />,
  router: <BranchesOutlined />,
  llm: <RobotOutlined />,
  tool: <ToolOutlined />,
  human: <CustomerServiceOutlined />,
  end: <FlagOutlined />,
}
